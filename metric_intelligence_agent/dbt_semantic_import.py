import re
from copy import deepcopy

from sqlglot import exp, parse_one

from metric_import import parse_metric_definitions_yaml, render_metric_definitions_yaml


class DbtSemanticImportError(ValueError):
    pass


def extract_dbt_semantic_document(manifest, *, relationship_resolutions=None):
    """Extract an ADR-010 document from a dbt manifest v10 or v11."""
    version = _manifest_version(manifest)
    if version not in (10, 11):
        return None

    nodes = {
        node_id: node
        for node_id, node in (manifest.get("nodes") or {}).items()
        if node.get("resource_type", "model") == "model" or node_id.startswith("model.")
    }
    entities = {}
    node_entities = {}
    model_names = {}
    relation_entities = {}
    warnings = []
    for node_id, node in nodes.items():
        name = str(node.get("name") or node.get("alias") or node_id.rsplit(".", 1)[-1])
        relation = node.get("relation_name") or node.get("alias") or name
        entities[name] = {
            "source": {"type": "table", "value": str(relation)},
            "keys": [],
        }
        node_entities[node_id] = name
        model_names[name.casefold()] = name
        relation_entities[str(relation).replace('"', "").split(".")[-1].casefold()] = name

    for node_id, node in nodes.items():
        entity_name = node_entities[node_id]
        for constraint, column in _node_constraints(node):
            key_type = constraint.get("type")
            if key_type == "primary_key" and column:
                _add_key(entities[entity_name], column, "primary")
            elif key_type == "unique" and column:
                _add_key(entities[entity_name], column, "unique")
            elif key_type == "foreign_key" and column:
                parent = _constraint_parent(
                    constraint,
                    relation_entities,
                    model_names,
                )
                if parent:
                    _apply_relationship(
                        entities,
                        {"entity": entity_name, "column": column},
                        parent,
                    )
                else:
                    warnings.append(
                        f"Skipped foreign-key constraint on {entity_name}.{column}: referenced entity is unresolved."
                    )

    dimensions = []
    measures = []
    raw_measures = {}
    for semantic in (manifest.get("semantic_models") or {}).values():
        entity_name = _semantic_model_entity(semantic, node_entities, model_names)
        if entity_name is None:
            warnings.append(
                f"Skipped semantic model {semantic.get('name', '<unnamed>')!r}: model entity is unresolved."
            )
            continue
        for semantic_entity in semantic.get("entities") or []:
            column = _plain_column(semantic_entity.get("expr"))
            key_type = semantic_entity.get("type")
            if column and key_type in {"primary", "unique", "natural"}:
                _add_key(entities[entity_name], column, key_type)
        for item in semantic.get("dimensions") or []:
            raw_name = item.get("name")
            column = _plain_column(item.get("expr") or raw_name)
            dimension_type = item.get("type")
            if not raw_name or not column or dimension_type not in {"categorical", "time"}:
                warnings.append(
                    f"Skipped incomplete dimension on entity {entity_name!r}."
                )
                continue
            dimension = {
                "name": _namespaced(entity_name, raw_name),
                "entity": entity_name,
                "column": column,
                "type": dimension_type,
            }
            if dimension_type == "time":
                type_params = item.get("type_params") or {}
                granularity = item.get("granularity") or type_params.get("time_granularity")
                if not granularity:
                    warnings.append(
                        f"Skipped time dimension {raw_name!r}: granularity is absent."
                    )
                    continue
                dimension["granularity"] = str(granularity)
            _append_named(dimensions, dimension)
        for item in semantic.get("measures") or []:
            raw_name = item.get("name")
            expression = item.get("expr") or raw_name
            aggregation = _aggregation(item.get("agg"))
            if not raw_name or not expression or aggregation is None:
                warnings.append(f"Skipped incomplete measure on entity {entity_name!r}.")
                continue
            measure = {
                "name": _namespaced(entity_name, raw_name),
                "entity": entity_name,
                "expression": _aggregate_expression(aggregation, str(expression)),
                "aggregation": aggregation,
                "re_aggregatable": aggregation in {"sum", "count"},
            }
            _append_named(measures, measure)
            raw_measures.setdefault(str(raw_name).casefold(), []).append(measure["name"])

    unresolved = []
    for test in _relationship_tests(manifest):
        relationship = _declared_relationship(test, nodes, node_entities, model_names)
        if relationship.get("resolved"):
            _apply_relationship(entities, relationship["child"], relationship["parent"])
        elif relationship.get("candidate"):
            _append_candidate(unresolved, relationship["candidate"])
        else:
            warnings.append(
                f"Skipped dbt relationship test {test.get('unique_id') or test.get('name', '<unnamed>')!r}: direction is unresolved."
            )

    resolutions = relationship_resolutions or {}
    for candidate in list(unresolved):
        resolution = resolutions.get(candidate["id"])
        if resolution:
            _apply_candidate_resolution(entities, candidate, resolution)
    unresolved = [item for item in unresolved if item["id"] not in resolutions]
    for entity in entities.values():
        entity["keys"].sort(key=_key_sort)

    metrics = {}
    notes = {}
    for metric in (manifest.get("metrics") or {}).values():
        name = metric.get("name")
        if not name:
            warnings.append("Skipped unnamed dbt metric.")
            continue
        mapped = _map_metric(metric, raw_measures, dimensions)
        if mapped is None:
            warnings.append(
                f"Skipped dbt metric {name!r}: it requires unresolved metric-to-metric composition or measure references."
            )
            continue
        metrics[name] = mapped
        notes[name] = {
            "description": str(metric.get("description") or ""),
            "business_rules": "",
            "caveats": "",
            "ambiguity_rules": "",
        }

    document = {
        "entities": entities,
        "dimensions": dimensions,
        "measures": measures,
        "metrics": metrics,
        "notes": notes,
    }
    document = parse_metric_definitions_yaml(render_metric_definitions_yaml(document))
    return {
        "document": document,
        "unresolved_keys": unresolved,
        "grounding": _grounding_references(document, unresolved),
        "warnings": warnings,
        "manifest_version": version,
    }


def _manifest_version(manifest):
    schema_url = str(manifest.get("metadata", {}).get("dbt_schema_version", ""))
    match = re.search(r"/manifest/v(\d+)\.json", schema_url)
    return int(match.group(1)) if match else None


def _semantic_model_entity(semantic, node_entities, model_names):
    for node_id in (semantic.get("depends_on") or {}).get("nodes", []):
        if node_id in node_entities:
            return node_entities[node_id]
    model = semantic.get("model")
    if isinstance(model, str):
        ref_name = _ref_name(model) or model
        return model_names.get(ref_name.casefold())
    return model_names.get(str(semantic.get("name") or "").casefold())


def _node_constraints(node):
    constraints = []
    for constraint in node.get("constraints") or []:
        columns = constraint.get("columns") or []
        if len(columns) == 1:
            constraints.append((constraint, str(columns[0])))
    for column_name, column in (node.get("columns") or {}).items():
        for constraint in column.get("constraints") or []:
            constraints.append((constraint, str(column.get("name") or column_name)))
    return constraints


def _constraint_parent(constraint, relation_entities, model_names):
    target_name = _ref_name(constraint.get("to"))
    target_columns = constraint.get("to_columns")
    if target_columns is None and constraint.get("to_column"):
        target_columns = [constraint["to_column"]]
    if target_name and isinstance(target_columns, list) and len(target_columns) == 1:
        entity = model_names.get(target_name.casefold())
        if entity:
            return {"entity": entity, "column": str(target_columns[0])}

    expression = constraint.get("expression")
    if not isinstance(expression, str):
        return None
    match = re.search(
        r"references\s+([A-Za-z0-9_.$\"`]+)\s*\(\s*([A-Za-z0-9_\"`]+)\s*\)",
        expression,
        re.IGNORECASE,
    )
    if not match:
        return None
    relation = match.group(1).replace('"', "").replace("`", "")
    entity = relation_entities.get(relation.split(".")[-1].casefold())
    if entity is None:
        return None
    return {
        "entity": entity,
        "column": match.group(2).replace('"', "").replace("`", ""),
    }


def _relationship_tests(manifest):
    tests = []
    for node_id, node in (manifest.get("nodes") or {}).items():
        metadata = node.get("test_metadata") or {}
        if node.get("resource_type") == "test" and metadata.get("name") == "relationships":
            copied = deepcopy(node)
            copied.setdefault("unique_id", node_id)
            tests.append(copied)
    for node_id, node in (manifest.get("unit_tests") or {}).items():
        metadata = node.get("test_metadata") or {}
        if metadata.get("name") == "relationships":
            copied = deepcopy(node)
            copied.setdefault("unique_id", node_id)
            tests.append(copied)
    return tests


def _declared_relationship(test, nodes, node_entities, model_names):
    metadata = test.get("test_metadata") or {}
    kwargs = metadata.get("kwargs") or {}
    child_id = test.get("attached_node")
    child_entity = node_entities.get(child_id)
    child_column = kwargs.get("column_name") or test.get("column_name")
    parent_name = _ref_name(kwargs.get("to"))
    parent_entity = model_names.get(parent_name.casefold()) if parent_name else None
    parent_column = _plain_column(kwargs.get("field"))

    dependency_entities = [
        node_entities[node_id]
        for node_id in (test.get("depends_on") or {}).get("nodes", [])
        if node_id in node_entities
    ]
    if child_entity is None and parent_entity and len(dependency_entities) == 2:
        others = [name for name in dependency_entities if name.casefold() != parent_entity.casefold()]
        child_entity = others[0] if len(others) == 1 else None
    if parent_entity is None and child_entity and len(dependency_entities) == 2:
        others = [name for name in dependency_entities if name.casefold() != child_entity.casefold()]
        parent_entity = others[0] if len(others) == 1 else None

    if child_entity and child_column and parent_entity and parent_column:
        return {
            "resolved": True,
            "child": {"entity": child_entity, "column": str(child_column)},
            "parent": {"entity": parent_entity, "column": parent_column},
        }

    if len(dependency_entities) == 2 and child_column and parent_column:
        left = {"entity": dependency_entities[0], "column": str(child_column)}
        right = {"entity": dependency_entities[1], "column": parent_column}
        return {"candidate": _relationship_candidate(left, right)}
    return {}


def _map_metric(metric, raw_measures, dimensions):
    metric_type = metric.get("type")
    params = metric.get("type_params") or {}
    filters = _metric_filters(metric, dimensions)
    if filters is None:
        return None
    if metric_type == "simple":
        measure = _resolved_measure(_reference_name(params.get("measure")), raw_measures)
        return {"type": "simple", "measure": measure, "filters": filters} if measure else None
    if metric_type == "ratio":
        numerator = _resolved_measure(_reference_name(params.get("numerator")), raw_measures)
        denominator = _resolved_measure(_reference_name(params.get("denominator")), raw_measures)
        if numerator and denominator:
            return {
                "type": "ratio",
                "numerator": numerator,
                "denominator": denominator,
                "filters": filters,
            }
        return None
    if metric_type == "cumulative":
        measure = _resolved_measure(_reference_name(params.get("measure")), raw_measures)
        window = params.get("window") or metric.get("window")
        if measure and window:
            return {
                "type": "cumulative",
                "measure": measure,
                "window": window,
                "filters": filters,
            }
        return None
    # Derived dbt metrics are metric-to-metric composition in supported manifests.
    return None


def _metric_filters(metric, dimensions):
    params = metric.get("type_params") or {}
    raw_filters = []
    for value in (metric.get("filter"), params.get("filter")):
        if value:
            raw_filters.append(value)
    for key in ("measure", "numerator", "denominator"):
        reference = params.get(key)
        if isinstance(reference, dict) and reference.get("filter"):
            raw_filters.append(reference["filter"])
    if not raw_filters:
        return []

    dimension_names = {item["name"].casefold(): item["name"] for item in dimensions}
    by_column = {}
    for item in dimensions:
        by_column.setdefault(item["column"].casefold(), []).append(item["name"])
    result = []
    for raw_filter in raw_filters:
        if isinstance(raw_filter, dict):
            raw_filter = raw_filter.get("where") or raw_filter.get("sql")
        if not isinstance(raw_filter, str):
            return None
        try:
            expression = parse_one(raw_filter)
        except Exception:
            return None
        for predicate in _and_terms(expression):
            column = predicate.find(exp.Column)
            if column is None:
                return None
            supplied_name = column.sql().casefold()
            dimension = dimension_names.get(supplied_name)
            if dimension is None:
                matches = by_column.get(column.name.casefold(), [])
                dimension = matches[0] if len(matches) == 1 else None
            if dimension is None:
                return None
            operator, value = _filter_parts(predicate, column)
            if operator is None:
                return None
            result.append(
                {"dimension": dimension, "operator": operator, "value": value}
            )
    return result


def _and_terms(expression):
    if isinstance(expression, exp.And):
        return [*_and_terms(expression.left), *_and_terms(expression.right)]
    return [expression]


def _filter_parts(predicate, column):
    operators = {
        exp.EQ: "=",
        exp.NEQ: "!=",
        exp.GT: ">",
        exp.GTE: ">=",
        exp.LT: "<",
        exp.LTE: "<=",
    }
    operator = next(
        (label for kind, label in operators.items() if isinstance(predicate, kind)),
        None,
    )
    if operator is None:
        return None, None
    values = [item for item in predicate.iter_expressions() if item is not column]
    if not values:
        return None, None
    value = values[-1]
    return operator, str(value.this) if isinstance(value, exp.Literal) else value.sql()


def _resolved_measure(raw_name, raw_measures):
    matches = raw_measures.get(str(raw_name or "").casefold(), [])
    return matches[0] if len(matches) == 1 else None


def _reference_name(reference):
    if isinstance(reference, str):
        return reference
    if isinstance(reference, dict):
        return reference.get("name") or reference.get("measure")
    return None


def _aggregation(value):
    value = str(value or "").casefold()
    return value if value in {"sum", "avg", "count", "count_distinct", "min", "max"} else None


def _aggregate_expression(aggregation, expression):
    if aggregation == "count_distinct":
        return f"COUNT(DISTINCT {expression})"
    return f"{aggregation.upper()}({expression})"


def _plain_column(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = parse_one(value)
    except Exception:
        return None
    if isinstance(parsed, exp.Column):
        return parsed.name
    return None


def _ref_name(value):
    if not isinstance(value, str):
        return None
    ref_match = re.search(r"ref\(\s*['\"]([^'\"]+)['\"]", value)
    if ref_match:
        return ref_match.group(1)
    source_match = re.search(
        r"source\(\s*['\"][^'\"]+['\"]\s*,\s*['\"]([^'\"]+)['\"]",
        value,
    )
    return source_match.group(1) if source_match else None


def _namespaced(entity, name):
    name = str(name)
    return name if "." in name else f"{entity}.{name}"


def _apply_relationship(entities, child, parent):
    _add_key(
        entities[child["entity"]],
        child["column"],
        "foreign",
        parent["entity"],
    )


def _apply_candidate_resolution(entities, candidate, resolution):
    if resolution == "left_references_right":
        child, parent = candidate["left"], candidate["right"]
    elif resolution == "right_references_left":
        child, parent = candidate["right"], candidate["left"]
    else:
        raise DbtSemanticImportError(
            f"Invalid relationship resolution {resolution!r} for {candidate['id']}."
        )
    _apply_relationship(entities, child, parent)


def _relationship_candidate(left, right):
    labels = sorted(
        (f"{left['entity']}.{left['column']}", f"{right['entity']}.{right['column']}"),
        key=str.casefold,
    )
    return {
        "id": f"relationship:{labels[0]}={labels[1]}",
        "kind": "relationship",
        "left": left,
        "right": right,
    }


def _add_key(entity, column, key_type, references_entity=None):
    entity["keys"] = [
        key
        for key in entity.get("keys", [])
        if key.get("column", "").casefold() != str(column).casefold()
    ]
    key = {"column": str(column), "type": key_type}
    if references_entity:
        key["references_entity"] = references_entity
    entity["keys"].append(key)


def _key_sort(key):
    order = {"primary": 0, "foreign": 1, "unique": 2, "natural": 3}
    return order[key["type"]], key["column"].casefold()


def _append_named(records, record):
    index = next(
        (
            index
            for index, item in enumerate(records)
            if item["name"].casefold() == record["name"].casefold()
        ),
        None,
    )
    if index is None:
        records.append(record)
    else:
        records[index] = record


def _append_candidate(candidates, candidate):
    if not any(item["id"].casefold() == candidate["id"].casefold() for item in candidates):
        candidates.append(candidate)


def _grounding_references(document, unresolved):
    tables = []
    columns = []
    seen = set()
    entity_tables = {}
    for name, entity in document["entities"].items():
        table = entity["source"]["value"]
        entity_tables[name.casefold()] = table
        if table not in tables:
            tables.append(table)
        for key in entity.get("keys", []):
            _append_column(columns, seen, table, key["column"])
    for dimension in document["dimensions"]:
        table = entity_tables[dimension["entity"].casefold()]
        _append_column(columns, seen, table, dimension["column"])
    for measure in document["measures"]:
        table = entity_tables[measure["entity"].casefold()]
        try:
            expression = parse_one(measure["expression"])
            for column in expression.find_all(exp.Column):
                _append_column(columns, seen, table, column.name)
        except Exception:
            pass
    for candidate in unresolved:
        for side in (candidate["left"], candidate["right"]):
            table = entity_tables.get(side["entity"].casefold())
            if table:
                _append_column(columns, seen, table, side["column"])
    return {"source_tables": tables, "column_references": columns}


def _append_column(columns, seen, table, column):
    key = (str(table).casefold(), str(column).casefold())
    if key not in seen:
        seen.add(key)
        columns.append({"table_name": str(table), "column_name": str(column)})
