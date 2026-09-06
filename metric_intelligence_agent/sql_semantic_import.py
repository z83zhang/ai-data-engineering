from copy import deepcopy

from sqlglot import exp, parse_one
from sqlglot.optimizer.scope import Scope, traverse_scope

from metric_import import parse_metric_definitions_yaml, render_metric_definitions_yaml


class SqlSemanticImportError(ValueError):
    pass


def extract_sql_semantic_document(
    sql,
    metric_name_hint="",
    *,
    key_resolutions=None,
    foreign_keys=None,
):
    """Extract an ADR-010 document and unresolved key candidates from SQL.

    SQL structure supplies CTE entity boundaries and GROUP BY primary keys. Join
    direction and other key classifications remain unresolved unless supplied by
    connector FK metadata or explicit analyst resolutions.
    """
    tree = parse_one(sql)
    scopes = list(traverse_scope(tree))
    if not scopes:
        raise SqlSemanticImportError("SQL did not contain a query scope.")
    root_scope = scopes[-1]
    root = root_scope.expression
    if not isinstance(root, exp.Select):
        raise SqlSemanticImportError("SQL-paste import currently requires a SELECT query.")

    ctes = list((root.args.get("with_") or exp.With()).expressions)
    cte_names = {cte.alias_or_name.casefold(): cte.alias_or_name for cte in ctes}
    entities = {}
    for cte in ctes:
        entities[cte.alias_or_name] = {
            "source": {"type": "query", "value": cte.this.sql()},
            "keys": [],
        }

    top_aliases, top_tables = _top_level_sources(root, cte_names)
    for table_name in top_tables:
        entities.setdefault(
            table_name,
            {"source": {"type": "table", "value": table_name}, "keys": []},
        )

    scope_by_sql = {scope.expression.sql(): scope for scope in scopes}
    lineage = {}
    measure_outputs = {}
    measures = []
    unresolved = []

    for cte in ctes:
        name = cte.alias_or_name
        query = cte.this
        scope = scope_by_sql.get(query.sql())
        aliases = _scope_aliases(scope, cte_names) if scope else {}
        projections = _projection_map(query)

        for output_name, expression in projections.items():
            aggregate = _single_aggregate(expression)
            if aggregate is not None:
                measure = _measure_for_aggregate(name, output_name, aggregate)
                _append_named(measures, measure)
                measure_outputs[(name.casefold(), output_name.casefold())] = measure["name"]
                lineage[(name.casefold(), output_name.casefold())] = (name, output_name)
                continue
            column = expression if isinstance(expression, exp.Column) else expression.find(exp.Column)
            if column is not None:
                lineage[(name.casefold(), output_name.casefold())] = _cte_output_origin(
                    name, output_name, column, aliases, lineage, entities
                )

        group = query.args.get("group")
        if group:
            for grouped in group.expressions:
                output_name = _projected_name_for_expression(projections, grouped)
                if output_name:
                    _add_key(entities[name], output_name, "primary")

        for join in query.args.get("joins") or []:
            for left, right in _equality_pairs(join.args.get("on")):
                for column in (left, right):
                    output_name = _projected_name_for_column(projections, column)
                    if output_name and not _has_key(entities[name], output_name):
                        _append_candidate(
                            unresolved,
                            {
                                "id": f"key:{name}.{output_name}",
                                "kind": "key_type",
                                "entity": name,
                                "column": output_name,
                                "choices": ["primary", "unique", "natural"],
                            },
                        )

    root_lineage = dict(lineage)
    root_alias_entities = {}
    for alias, source_name in top_aliases.items():
        root_alias_entities[alias] = source_name

    relationship_candidates = []
    for join in root.args.get("joins") or []:
        for left, right in _equality_pairs(join.args.get("on")):
            left_ref = _resolve_column(left, root_alias_entities, root_lineage)
            right_ref = _resolve_column(right, root_alias_entities, root_lineage)
            if left_ref == right_ref or left_ref[0] not in entities or right_ref[0] not in entities:
                continue
            candidate = {
                "id": _relationship_id(left_ref, right_ref),
                "kind": "relationship",
                "left": {"entity": left_ref[0], "column": left_ref[1]},
                "right": {"entity": right_ref[0], "column": right_ref[1]},
            }
            _append_candidate(relationship_candidates, candidate)

    resolutions = key_resolutions or {}
    declared_foreign_keys = foreign_keys or []
    for candidate in relationship_candidates:
        resolution = _fk_resolution(candidate, declared_foreign_keys) or resolutions.get(
            candidate["id"]
        )
        if resolution:
            _apply_relationship_resolution(entities, candidate, resolution)
        else:
            unresolved.append(candidate)
    for candidate in list(unresolved):
        if candidate["kind"] != "key_type":
            continue
        resolution = resolutions.get(candidate["id"])
        if resolution:
            if resolution not in candidate["choices"]:
                raise SqlSemanticImportError(
                    f"Invalid resolution {resolution!r} for {candidate['id']}."
                )
            _add_key(entities[candidate["entity"]], candidate["column"], resolution)
    unresolved = [item for item in unresolved if item["id"] not in resolutions]
    for entity in entities.values():
        entity["keys"].sort(
            key=lambda key: (
                {"primary": 0, "foreign": 1, "unique": 2, "natural": 3}[key["type"]],
                key["column"].casefold(),
            )
        )

    dimensions, filters = _top_level_filters(root, root_alias_entities, lineage)
    grain = _top_level_grain(root, root_alias_entities, lineage, dimensions)
    metrics = {}
    top_aggregates = []
    for selection in root.expressions:
        value = selection.this if isinstance(selection, exp.Alias) else selection
        if value.find(exp.AggFunc):
            top_aggregates.append((selection.alias_or_name, value))
    if not top_aggregates:
        raise SqlSemanticImportError(
            "No aggregate metric expressions were found in the top-level SELECT list."
        )

    multiple = len(top_aggregates) > 1
    for index, (alias, expression) in enumerate(top_aggregates, start=1):
        metric_name = alias or (metric_name_hint.strip() if not multiple else "")
        if not multiple and metric_name_hint.strip():
            metric_name = metric_name_hint.strip()
        if not metric_name:
            metric_name = f"metric_{index}"
        metric, new_measures = _metric_from_expression(
            metric_name,
            expression,
            root_alias_entities,
            lineage,
            measure_outputs,
        )
        for measure in new_measures:
            _append_named(measures, measure)
        metric["filters"] = deepcopy(filters)
        if grain:
            metric["grain"] = list(grain)
        metrics[metric_name] = metric

    document = {
        "entities": entities,
        "dimensions": dimensions,
        "measures": measures,
        "metrics": metrics,
        "notes": {
            name: {
                "description": "",
                "business_rules": "",
                "caveats": "",
                "ambiguity_rules": "",
                "provenance": "system_generated",
            }
            for name in metrics
        },
    }
    # Phase 1 is the sole document validator; YAML round-trip catches shape drift.
    document = parse_metric_definitions_yaml(render_metric_definitions_yaml(document))
    grounding = _grounding_references(scopes, cte_names)
    return {
        "document": document,
        "unresolved_keys": unresolved,
        "grounding": grounding,
        "metric_count": len(metrics),
    }


def apply_confirmed_entity_keys(reviewed_document, confirmed_document):
    """Make confirmed SQL key resolutions authoritative over stale review state."""
    reviewed = deepcopy(reviewed_document)
    confirmed_entities = confirmed_document.get("entities", {})
    for confirmed_name, confirmed_entity in confirmed_entities.items():
        reviewed_name = next(
            (
                name
                for name in reviewed.get("entities", {})
                if name.casefold() == confirmed_name.casefold()
            ),
            None,
        )
        if reviewed_name is None:
            raise SqlSemanticImportError(
                f"Reviewed YAML is missing confirmed entity {confirmed_name!r}."
            )
        reviewed["entities"][reviewed_name]["keys"] = deepcopy(
            confirmed_entity.get("keys", [])
        )
    return parse_metric_definitions_yaml(render_metric_definitions_yaml(reviewed))


def _top_level_sources(root, cte_names):
    aliases = {}
    real_tables = []
    expressions = []
    from_clause = root.args.get("from_")
    if from_clause and from_clause.this is not None:
        expressions.append(from_clause.this)
    expressions.extend(join.this for join in root.args.get("joins") or [])
    for source in expressions:
        if not isinstance(source, exp.Table):
            continue
        source_name = source.name
        if source_name.casefold() in cte_names:
            entity_name = cte_names[source_name.casefold()]
        else:
            entity_name = _qualified_table(source)
            if entity_name not in real_tables:
                real_tables.append(entity_name)
        aliases[source.alias_or_name.casefold()] = entity_name
        aliases[source_name.casefold()] = entity_name
    return aliases, real_tables


def _scope_aliases(scope, cte_names):
    aliases = {}
    if scope is None:
        return aliases
    for alias, source in scope.sources.items():
        if isinstance(source, exp.Table):
            name = source.name
            aliases[alias.casefold()] = cte_names.get(name.casefold(), _qualified_table(source))
        elif isinstance(source, Scope):
            aliases[alias.casefold()] = alias
    return aliases


def _projection_map(query):
    projections = {}
    for selection in query.expressions:
        name = selection.alias_or_name
        if name:
            projections[name] = selection.this if isinstance(selection, exp.Alias) else selection
    return projections


def _resolve_column(column, aliases, lineage, fallback_entity=None):
    qualifier = column.table.casefold() if column.table else ""
    entity = aliases.get(qualifier) if qualifier else None
    if entity is None and len(set(aliases.values())) == 1:
        entity = next(iter(aliases.values()))
    if entity is None:
        entity = fallback_entity or "<unqualified>"
    return lineage.get((entity.casefold(), column.name.casefold()), (entity, column.name))


def _cte_output_origin(cte_name, output_name, column, aliases, lineage, entities):
    source_entity = aliases.get(column.table.casefold()) if column.table else None
    if source_entity is None and len(set(aliases.values())) == 1:
        source_entity = next(iter(aliases.values()))
    if source_entity in entities:
        return lineage.get(
            (source_entity.casefold(), column.name.casefold()),
            (source_entity, column.name),
        )
    return cte_name, output_name


def _single_aggregate(expression):
    aggregates = list(expression.find_all(exp.AggFunc))
    return aggregates[0] if len(aggregates) == 1 and expression is aggregates[0] else None


def _aggregate_name(aggregate):
    if isinstance(aggregate, exp.Count) and isinstance(aggregate.this, exp.Distinct):
        return "count_distinct"
    name = aggregate.key.casefold()
    return {"average": "avg"}.get(name, name)


def _measure_for_aggregate(entity, output_name, aggregate):
    aggregation = _aggregate_name(aggregate)
    return {
        "name": f"{entity}.{output_name}_{aggregation}",
        "entity": entity,
        "expression": aggregate.sql(),
        "aggregation": aggregation if aggregation in {
            "sum", "avg", "count", "count_distinct", "min", "max"
        } else "custom",
        "re_aggregatable": aggregation in {"sum", "count"},
    }


def _metric_from_expression(name, expression, aliases, lineage, measure_outputs):
    aggregates = list(expression.find_all(exp.AggFunc))
    existing_refs = []
    new_measures = []
    replacements = []
    for index, aggregate in enumerate(aggregates, start=1):
        column = aggregate.this if isinstance(aggregate.this, exp.Column) else aggregate.find(exp.Column)
        resolved = _resolve_column(column, aliases, lineage) if column is not None else ("<unqualified>", "value")
        existing = measure_outputs.get((resolved[0].casefold(), resolved[1].casefold()))
        if existing:
            measure_name = existing
            replacement = aggregate.sql().replace(column.sql(), measure_name, 1)
        else:
            output_name = name if len(aggregates) == 1 else f"{name}_{index}"
            measure = _measure_for_aggregate(resolved[0], output_name, aggregate)
            measure_name = measure["name"]
            new_measures.append(measure)
            replacement = measure_name
        existing_refs.append(measure_name)
        replacements.append((aggregate.sql(), replacement))

    first_column = aggregates[0].find(exp.Column) if aggregates else None
    first_resolved = _resolve_column(first_column, aliases, lineage) if first_column else None
    if len(aggregates) == 1 and not (
        first_resolved
        and measure_outputs.get((first_resolved[0].casefold(), first_resolved[1].casefold()))
    ):
        return {"type": "simple", "measure": existing_refs[0]}, new_measures

    if isinstance(expression, exp.Div) and len(existing_refs) == 2:
        return {
            "type": "ratio",
            "numerator": existing_refs[0],
            "denominator": existing_refs[1],
        }, new_measures

    derived_sql = expression.sql()
    for aggregate_sql, measure_name in replacements:
        derived_sql = derived_sql.replace(aggregate_sql, measure_name, 1)
    return {"type": "derived", "expression": derived_sql}, new_measures


def _top_level_filters(root, aliases, lineage):
    dimensions = []
    filters = []
    where = root.args.get("where")
    predicates = _and_terms(where.this) if where else []
    for predicate in predicates:
        column = predicate.find(exp.Column)
        if column is None:
            continue
        entity, column_name = _resolve_column(column, aliases, lineage)
        dimension_name = f"{entity}.{column_name}"
        _append_named(
            dimensions,
            {
                "name": dimension_name,
                "entity": entity,
                "column": column_name,
                "type": "categorical",
            },
        )
        operator, value = _filter_parts(predicate, column)
        filters.append(
            {"dimension": dimension_name, "operator": operator, "value": value}
        )
    return dimensions, filters


def _top_level_grain(root, aliases, lineage, dimensions):
    grain = []
    group = root.args.get("group")
    for grouped in group.expressions if group else []:
        column = grouped if isinstance(grouped, exp.Column) else grouped.find(exp.Column)
        if column is None:
            continue
        entity, column_name = _resolve_column(column, aliases, lineage)
        name = f"{entity}.{column_name}"
        _append_named(
            dimensions,
            {"name": name, "entity": entity, "column": column_name, "type": "categorical"},
        )
        grain.append(name)
    return grain


def _filter_parts(predicate, column):
    if isinstance(predicate, exp.Not) and isinstance(predicate.this, exp.In):
        values = ", ".join(_filter_value(item) for item in predicate.this.expressions)
        return "NOT IN", values
    operators = {
        exp.EQ: "=", exp.NEQ: "!=", exp.GT: ">", exp.GTE: ">=",
        exp.LT: "<", exp.LTE: "<=", exp.In: "IN", exp.Like: "LIKE",
    }
    operator = next((label for kind, label in operators.items() if isinstance(predicate, kind)), predicate.key.upper())
    values = [item for item in predicate.iter_expressions() if item is not column]
    value = _filter_value(values[-1]) if values else ""
    return operator, value


def _filter_value(expression):
    if isinstance(expression, exp.Literal):
        return str(expression.this)
    return expression.sql()


def _and_terms(expression):
    if isinstance(expression, exp.And):
        return [*_and_terms(expression.left), *_and_terms(expression.right)]
    return [expression]


def _equality_pairs(expression):
    if expression is None:
        return []
    pairs = []
    for equality in expression.find_all(exp.EQ):
        if isinstance(equality.left, exp.Column) and isinstance(equality.right, exp.Column):
            pairs.append((equality.left, equality.right))
    return pairs


def _projected_name_for_expression(projections, expression):
    target = expression.sql().casefold()
    return next((name for name, value in projections.items() if value.sql().casefold() == target), None)


def _projected_name_for_column(projections, column):
    target = column.sql().casefold()
    for name, value in projections.items():
        if isinstance(value, exp.Column) and value.sql().casefold() == target:
            return name
    return None


def _relationship_id(left, right):
    values = sorted((f"{left[0]}.{left[1]}", f"{right[0]}.{right[1]}"), key=str.casefold)
    return f"relationship:{values[0]}={values[1]}"


def _apply_relationship_resolution(entities, candidate, resolution):
    if resolution == "left_references_right":
        child, parent = candidate["left"], candidate["right"]
    elif resolution == "right_references_left":
        child, parent = candidate["right"], candidate["left"]
    else:
        raise SqlSemanticImportError(
            f"Invalid relationship resolution {resolution!r} for {candidate['id']}."
        )
    _add_key(entities[child["entity"]], child["column"], "foreign", parent["entity"])
    _add_key(entities[parent["entity"]], parent["column"], "primary")


def _fk_resolution(candidate, foreign_keys):
    left = candidate["left"]
    right = candidate["right"]
    for foreign_key in foreign_keys:
        child = (foreign_key.get("from_entity", "").casefold(), foreign_key.get("column", "").casefold())
        parent = (foreign_key.get("references_entity", "").casefold(), foreign_key.get("references_column", "").casefold())
        left_key = (left["entity"].casefold(), left["column"].casefold())
        right_key = (right["entity"].casefold(), right["column"].casefold())
        if child == left_key and parent == right_key:
            return "left_references_right"
        if child == right_key and parent == left_key:
            return "right_references_left"
    return None


def _add_key(entity, column, key_type, references_entity=None):
    entity["keys"] = [
        key for key in entity.get("keys", []) if key.get("column", "").casefold() != column.casefold()
    ]
    key = {"column": column, "type": key_type}
    if references_entity:
        key["references_entity"] = references_entity
    entity["keys"].append(key)


def _has_key(entity, column):
    return any(key.get("column", "").casefold() == column.casefold() for key in entity.get("keys", []))


def _append_candidate(candidates, candidate):
    if not any(item["id"].casefold() == candidate["id"].casefold() for item in candidates):
        candidates.append(candidate)


def _append_named(records, record):
    existing = next(
        (index for index, item in enumerate(records) if item["name"].casefold() == record["name"].casefold()),
        None,
    )
    if existing is None:
        records.append(record)
    else:
        records[existing] = record


def _grounding_references(scopes, cte_names):
    tables = []
    columns = []
    seen_columns = set()
    for scope in scopes:
        aliases = {}
        for alias, source in scope.sources.items():
            if isinstance(source, exp.Table) and source.name.casefold() not in cte_names:
                table_name = _qualified_table(source)
                aliases[alias.casefold()] = table_name
                if table_name not in tables:
                    tables.append(table_name)
        for column in scope.columns:
            table_name = aliases.get(column.table.casefold()) if column.table else None
            if table_name is None and not column.table and len(set(aliases.values())) == 1:
                table_name = next(iter(aliases.values()))
            if table_name is None:
                continue
            key = (table_name.casefold(), column.name.casefold())
            if key not in seen_columns:
                seen_columns.add(key)
                columns.append({"table_name": table_name, "column_name": column.name})
    return {"source_tables": tables, "column_references": columns}


def _qualified_table(table):
    return ".".join(part for part in (table.catalog, table.db, table.name) if part)
