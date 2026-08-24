import re
from copy import deepcopy
from pathlib import Path

import yaml
from sqlglot import exp, parse_one


FACT_SECTIONS = ("entities", "dimensions", "measures", "metrics")
DOCUMENT_SECTIONS = (*FACT_SECTIONS, "notes")
NOTE_FIELDS = ("description", "business_rules", "caveats", "ambiguity_rules")
KEY_TYPES = {"primary", "foreign", "unique", "natural"}
DIMENSION_TYPES = {"categorical", "time"}
AGGREGATIONS = {"sum", "avg", "count", "count_distinct", "min", "max", "custom"}
METRIC_TYPES = {"simple", "ratio", "derived", "cumulative"}


class MetricDefinitionError(ValueError):
    """Base error for invalid layered metric-definition documents."""


class RenameConfirmationRequired(MetricDefinitionError):
    def __init__(self, old_name, new_name):
        self.old_name = old_name
        self.new_name = new_name
        super().__init__(f"Renaming metric {old_name!r} to {new_name!r} requires confirmation.")


class RelationshipConflictError(MetricDefinitionError):
    def __init__(self, conflicts):
        self.conflicts = conflicts
        details = "; ".join(conflict["message"] for conflict in conflicts)
        super().__init__(f"Entity relationship changes require confirmation: {details}")


def empty_metric_document():
    return {"entities": {}, "dimensions": [], "measures": [], "metrics": {}, "notes": {}}


def parse_metric_definitions_yaml(content):
    """Parse and validate an ADR-010 metric-definition YAML document."""
    try:
        loaded = yaml.safe_load(content) if content.strip() else {}
    except yaml.YAMLError as error:
        raise MetricDefinitionError(f"Invalid metric-definition YAML: {error}") from error
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise MetricDefinitionError("Metric-definition YAML must contain a mapping.")
    unknown = set(loaded) - set(DOCUMENT_SECTIONS)
    if unknown:
        raise MetricDefinitionError(f"Unknown top-level sections: {', '.join(sorted(unknown))}")
    document = empty_metric_document()
    document.update(deepcopy(loaded))
    validate_metric_document(document)
    return document


def render_metric_definitions_yaml(document):
    """Validate and serialize an ADR-010 metric-definition document."""
    normalized = _normalized_document(document)
    validate_metric_document(normalized)
    return yaml.safe_dump(normalized, sort_keys=False, allow_unicode=True)


def load_metric_definitions_yaml(path):
    path = Path(path)
    return parse_metric_definitions_yaml(path.read_text(encoding="utf-8"))


def save_metric_definitions_yaml(path, document):
    """Validate and atomically write a metric-definition YAML document."""
    path = Path(path)
    content = render_metric_definitions_yaml(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def merge_metric_documents(
    existing,
    incoming,
    *,
    metric_renames=None,
    confirm_renames=False,
    confirm_relationship_conflicts=False,
):
    """Merge imported facts while preserving analyst notes independently.

    Incoming fact records replace case-insensitive name matches. Metric renames and
    relationship-changing entity replacements require explicit confirmation.
    Incoming notes are ignored; use ``update_metric_notes`` for analyst edits.
    """
    current = _normalized_document(existing)
    additions = _normalized_document(incoming)
    validate_metric_document(current)
    renames = metric_renames or {}
    if renames and not confirm_renames:
        old_name, new_name = next(iter(renames.items()))
        raise RenameConfirmationRequired(old_name, new_name)

    conflicts = relationship_conflicts(current, additions)
    if conflicts and not confirm_relationship_conflicts:
        raise RelationshipConflictError(conflicts)

    merged = deepcopy(current)
    for old_name, new_name in renames.items():
        _apply_metric_rename(merged, additions, old_name, new_name)
    _merge_named_mapping(merged["entities"], additions["entities"])
    _merge_named_list(merged["dimensions"], additions["dimensions"], "dimension")
    _merge_named_list(merged["measures"], additions["measures"], "measure")
    _merge_named_mapping(merged["metrics"], additions["metrics"])
    validate_metric_document(merged)
    return merged


def update_metric_notes(document, metric_name, **notes):
    """Apply an explicit analyst note edit without changing metric facts."""
    unknown = set(notes) - set(NOTE_FIELDS)
    if unknown:
        raise MetricDefinitionError(f"Unknown note fields: {', '.join(sorted(unknown))}")
    updated = _normalized_document(document)
    actual_name = _case_insensitive_name(updated["metrics"], metric_name)
    if actual_name is None:
        raise MetricDefinitionError(f"Cannot attach notes to unknown metric {metric_name!r}.")
    existing_name = _case_insensitive_name(updated["notes"], actual_name)
    current_notes = deepcopy(updated["notes"].pop(existing_name, {})) if existing_name else {}
    current_notes.update(notes)
    updated["notes"][actual_name] = current_notes
    validate_metric_document(updated)
    return updated


def relationship_conflicts(existing, incoming):
    """Return relationship-level conflicts for same-name entity replacements."""
    current = _normalized_document(existing)["entities"]
    additions = _normalized_document(incoming)["entities"]
    conflicts = []
    for incoming_name, incoming_entity in additions.items():
        current_name = _case_insensitive_name(current, incoming_name)
        if current_name is None:
            continue
        old_keys = {key.get("column", "").casefold(): key for key in current[current_name].get("keys", [])}
        new_keys = {key.get("column", "").casefold(): key for key in incoming_entity.get("keys", [])}
        for column, old_key in old_keys.items():
            new_key = new_keys.get(column)
            dropped_foreign = old_key.get("type") == "foreign" and new_key is None
            changed = new_key is not None and (
                old_key.get("type") != new_key.get("type")
                or str(old_key.get("references_entity") or "").casefold()
                != str(new_key.get("references_entity") or "").casefold()
            )
            if dropped_foreign or changed:
                conflicts.append(
                    {
                        "entity": current_name,
                        "column": old_key.get("column", ""),
                        "existing": deepcopy(old_key),
                        "incoming": deepcopy(new_key),
                        "message": f"{current_name}.{old_key.get('column', '')} changed or was dropped",
                    }
                )
    return conflicts


def validate_metric_document(document):
    """Enforce ADR-010 structure and document-internal references."""
    if not isinstance(document, dict):
        raise MetricDefinitionError("Metric definitions must be a mapping.")
    for section in DOCUMENT_SECTIONS:
        if section not in document:
            raise MetricDefinitionError(f"Missing top-level section {section!r}.")
    if not isinstance(document["entities"], dict) or not isinstance(document["metrics"], dict):
        raise MetricDefinitionError("Entities and metrics must be mappings keyed by name.")
    if not isinstance(document["dimensions"], list) or not isinstance(document["measures"], list):
        raise MetricDefinitionError("Dimensions and measures must be lists.")
    if not isinstance(document["notes"], dict):
        raise MetricDefinitionError("Notes must be a mapping keyed by metric name.")

    _assert_unique_names(document["entities"].keys(), "entity")
    _assert_named_records(document["dimensions"], "dimension")
    _assert_named_records(document["measures"], "measure")
    _assert_unique_names(document["metrics"].keys(), "metric")
    _assert_unique_names(document["notes"].keys(), "note")
    entity_names = {name.casefold(): name for name in document["entities"]}

    entity_keys = set()
    for name, entity in document["entities"].items():
        _validate_entity(name, entity, entity_names, entity_keys)

    dimension_names = set()
    for dimension in document["dimensions"]:
        name = _required_string(dimension, "name", "Dimension")
        entity = _required_string(dimension, "entity", f"Dimension {name!r}")
        _require_entity(entity, entity_names, f"Dimension {name!r}")
        _required_string(dimension, "column", f"Dimension {name!r}")
        dimension_type = dimension.get("type")
        if dimension_type not in DIMENSION_TYPES:
            raise MetricDefinitionError(f"Dimension {name!r} has invalid type {dimension_type!r}.")
        if dimension_type == "time" and not dimension.get("granularity"):
            raise MetricDefinitionError(f"Time dimension {name!r} requires granularity.")
        dimension_names.add(name.casefold())

    measures = {}
    for measure in document["measures"]:
        name = _required_string(measure, "name", "Measure")
        entity = _required_string(measure, "entity", f"Measure {name!r}")
        _require_entity(entity, entity_names, f"Measure {name!r}")
        _required_string(measure, "expression", f"Measure {name!r}")
        if measure.get("aggregation") not in AGGREGATIONS:
            raise MetricDefinitionError(f"Measure {name!r} has invalid aggregation.")
        if not isinstance(measure.get("re_aggregatable"), bool):
            raise MetricDefinitionError(f"Measure {name!r} requires boolean re_aggregatable.")
        measures[name.casefold()] = measure

    allowed_filters = dimension_names | entity_keys
    for name, metric in document["metrics"].items():
        _validate_metric(name, metric, measures, allowed_filters)
    for name, notes in document["notes"].items():
        if _case_insensitive_name(document["metrics"], name) is None:
            raise MetricDefinitionError(f"Notes reference undeclared metric {name!r}.")
        if not isinstance(notes, dict):
            raise MetricDefinitionError(f"Notes for metric {name!r} must be a mapping.")
        unknown = set(notes) - set(NOTE_FIELDS)
        if unknown:
            raise MetricDefinitionError(
                f"Notes for metric {name!r} contain unknown fields: "
                f"{', '.join(sorted(unknown))}."
            )
        if any(not isinstance(value, str) for value in notes.values()):
            raise MetricDefinitionError(f"Notes for metric {name!r} must contain text values.")
    return document


def _normalized_document(document):
    if not isinstance(document, dict):
        raise MetricDefinitionError("Metric definitions must be a mapping.")
    unknown = set(document) - set(DOCUMENT_SECTIONS)
    if unknown:
        raise MetricDefinitionError(f"Unknown top-level sections: {', '.join(sorted(unknown))}")
    normalized = empty_metric_document()
    for section in DOCUMENT_SECTIONS:
        if section in document:
            normalized[section] = deepcopy(document[section])
    return normalized


def _validate_entity(name, entity, entity_names, entity_keys):
    if not isinstance(entity, dict):
        raise MetricDefinitionError(f"Entity {name!r} must be a mapping.")
    source = entity.get("source")
    if not isinstance(source, dict) or source.get("type") not in {"table", "query"} or not source.get("value"):
        raise MetricDefinitionError(f"Entity {name!r} requires a table or query source.")
    keys = entity.get("keys", [])
    if not isinstance(keys, list):
        raise MetricDefinitionError(f"Entity {name!r} keys must be a list.")
    seen = set()
    for key in keys:
        column = _required_string(key, "column", f"Entity {name!r} key")
        if column.casefold() in seen:
            raise MetricDefinitionError(f"Entity {name!r} has duplicate key column {column!r}.")
        seen.add(column.casefold())
        key_type = key.get("type")
        if key_type not in KEY_TYPES:
            raise MetricDefinitionError(f"Entity {name!r} key {column!r} has invalid type.")
        reference = key.get("references_entity")
        if key_type == "foreign":
            if not reference:
                raise MetricDefinitionError(f"Foreign key {name}.{column} requires references_entity.")
            _require_entity(reference, entity_names, f"Foreign key {name}.{column}")
        elif reference:
            raise MetricDefinitionError(f"Non-foreign key {name}.{column} cannot reference an entity.")
        entity_keys.add(f"{name}.{column}".casefold())


def _validate_metric(name, metric, measures, allowed_filters):
    if not isinstance(metric, dict):
        raise MetricDefinitionError(f"Metric {name!r} must be a mapping.")
    metric_type = metric.get("type")
    if metric_type not in METRIC_TYPES:
        raise MetricDefinitionError(f"Metric {name!r} has invalid type {metric_type!r}.")
    if metric_type == "simple":
        _require_measure(metric.get("measure"), measures, name)
    elif metric_type == "ratio":
        _require_measure(metric.get("numerator"), measures, name)
        _require_measure(metric.get("denominator"), measures, name)
    elif metric_type == "derived":
        expression = _required_string(metric, "expression", f"Metric {name!r}")
        _validate_derived_expression(name, expression, measures)
    elif metric_type == "cumulative" and not metric.get("window"):
        raise MetricDefinitionError(f"Cumulative metric {name!r} requires window.")
    for item in metric.get("filters", []):
        dimension = _required_string(item, "dimension", f"Metric {name!r} filter")
        if dimension.casefold() not in allowed_filters:
            raise MetricDefinitionError(
                f"Metric {name!r} filter references undeclared dimension or entity key {dimension!r}."
            )


def _validate_derived_expression(metric_name, expression, measures):
    try:
        tree = parse_one(expression)
    except Exception as error:
        raise MetricDefinitionError(f"Metric {metric_name!r} has invalid derived expression.") from error
    resolved_measure = False
    for aggregate in tree.find_all(exp.AggFunc):
        aggregate_sql = aggregate.sql()
        for measure_name, measure in measures.items():
            if not _contains_qualified_name(aggregate_sql, measure_name):
                continue
            resolved_measure = True
            if not measure["re_aggregatable"]:
                raise MetricDefinitionError(
                    f"Metric {metric_name!r} further-aggregates non-re-aggregatable measure "
                    f"{measure.get('name')!r}."
                )
    if not resolved_measure:
        raise MetricDefinitionError(
            f"Metric {metric_name!r} derived expression must aggregate at least one "
            "declared measure."
        )


def _contains_qualified_name(expression, casefolded_name):
    return re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(casefolded_name)}(?![A-Za-z0-9_])",
        expression.casefold(),
    ) is not None


def _require_measure(name, measures, metric_name):
    if not isinstance(name, str) or name.casefold() not in measures:
        raise MetricDefinitionError(f"Metric {metric_name!r} references undeclared measure {name!r}.")


def _require_entity(name, entity_names, owner):
    if not isinstance(name, str) or name.casefold() not in entity_names:
        raise MetricDefinitionError(f"{owner} references undeclared entity {name!r}.")


def _required_string(mapping, field, owner):
    if not isinstance(mapping, dict) or not isinstance(mapping.get(field), str) or not mapping[field].strip():
        raise MetricDefinitionError(f"{owner} requires non-empty {field!r}.")
    return mapping[field]


def _assert_unique_names(names, kind):
    seen = set()
    for name in names:
        if not isinstance(name, str) or not name.strip():
            raise MetricDefinitionError(f"Every {kind} requires a non-empty name.")
        folded = name.casefold()
        if folded in seen:
            raise MetricDefinitionError(f"Duplicate case-insensitive {kind} name {name!r}.")
        seen.add(folded)


def _assert_named_records(records, kind):
    if any(not isinstance(record, dict) for record in records):
        raise MetricDefinitionError(f"Every {kind} must be a mapping.")
    _assert_unique_names((record.get("name") for record in records), kind)


def _merge_named_mapping(existing, incoming):
    for name, value in incoming.items():
        old_name = _case_insensitive_name(existing, name)
        if old_name is not None:
            existing.pop(old_name)
        existing[name] = deepcopy(value)


def _merge_named_list(existing, incoming, kind):
    _assert_named_records(incoming, kind)
    incoming_names = {item["name"].casefold() for item in incoming}
    existing[:] = [item for item in existing if item["name"].casefold() not in incoming_names]
    existing.extend(deepcopy(incoming))


def _case_insensitive_name(mapping, name):
    folded = name.casefold()
    return next((current for current in mapping if current.casefold() == folded), None)


def _apply_metric_rename(document, incoming, old_name, new_name):
    current_name = _case_insensitive_name(document["metrics"], old_name)
    incoming_name = _case_insensitive_name(incoming["metrics"], new_name)
    if current_name is None or incoming_name is None:
        raise MetricDefinitionError(
            f"Rename requires existing metric {old_name!r} and incoming metric {new_name!r}."
        )
    if old_name.casefold() == new_name.casefold():
        return
    document["metrics"].pop(current_name)
    note_name = _case_insensitive_name(document["notes"], current_name)
    if note_name is not None:
        notes = document["notes"].pop(note_name)
        target_note = _case_insensitive_name(document["notes"], new_name)
        if target_note is not None:
            raise MetricDefinitionError(f"Cannot rename metric: notes already exist for {new_name!r}.")
        document["notes"][new_name] = notes


def render_metric_markdown(metrics):
    sections = ["# Metric Definitions"]
    for metric in metrics:
        columns = ", ".join(
            f"{item['table_name']}.{item['column_name']}"
            for item in metric["column_references"]
        )
        fields = [
            ("Definition", metric.get("description", "")),
            ("Formula", metric.get("formula", "")),
            ("Source tables", ", ".join(metric["source_tables"])),
            ("Column references", columns),
            ("Join conditions", "; ".join(metric["join_conditions"])),
            ("Filters", "; ".join(metric["filters"])),
            ("Grain", metric.get("grain", "")),
            ("Business rules", metric.get("business_rules", "")),
            ("Trust level", metric.get("trust_level", "")),
        ]
        lines = [f"## {metric.get('name') or 'Unnamed metric'}"]
        lines.extend(f"- {label}: {value}" for label, value in fields)
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def merge_metric_definitions(existing, incoming):
    """Replace all case-insensitive heading matches and retain one new section."""
    existing_sections = _metric_markdown_sections(existing)
    incoming_sections = _metric_markdown_sections(incoming)
    if not incoming_sections:
        raise ValueError("Metric definitions must contain at least one level-two heading.")

    replacements = {}
    for name, section in incoming_sections:
        replacements[name.casefold()] = (name, section)
    kept = [
        section
        for name, section in existing_sections
        if name.casefold() not in replacements
    ]
    merged_sections = kept + [section for _, section in replacements.values()]
    return "# Metric Definitions\n\n" + "\n\n".join(merged_sections) + "\n"


def _metric_markdown_sections(content):
    headings = list(re.finditer(r"(?m)^##[ \t]+(.+?)[ \t]*$", content))
    sections = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
        sections.append(
            (heading.group(1).strip(), content[heading.start() : end].strip())
        )
    return sections


def parse_sql_facts(sql):
    """Extract metric facts that are explicit in a SQL statement."""
    tree = parse_one(sql)
    tables, aliases = _sql_tables_and_aliases(tree)
    columns = _column_references([tree], tables, aliases)

    formulas = []
    select = tree.find(exp.Select)
    if select:
        for expression in select.expressions:
            value = expression.this if isinstance(expression, exp.Alias) else expression
            if value.find(exp.AggFunc):
                formulas.append(value.sql())

    filters = []
    for clause_type in (exp.Where, exp.Having):
        clause = tree.find(clause_type)
        if clause is not None:
            filters.append(clause.this.sql())

    joins = [join.args["on"].sql() for join in tree.find_all(exp.Join) if join.args.get("on")]
    group = tree.find(exp.Group)
    group_by = [item.sql() for item in group.expressions] if group else []
    return {
        "source_tables": tables,
        "column_references": columns,
        "formulas": formulas,
        "join_conditions": joins,
        "filters": filters,
        "group_by": group_by,
    }


def parse_sql_metric_facts(sql):
    """Return one fact set per aggregate expression in the top-level SELECT."""
    tree = parse_one(sql)
    shared = parse_sql_facts(sql)
    tables, aliases = _sql_tables_and_aliases(tree)
    shared_expressions = []
    for clause_type in (exp.Where, exp.Having):
        clause = tree.find(clause_type)
        if clause is not None:
            shared_expressions.append(clause.this)
    shared_expressions.extend(
        join.args["on"] for join in tree.find_all(exp.Join) if join.args.get("on")
    )
    group = tree.find(exp.Group)
    if group:
        shared_expressions.extend(group.expressions)

    metrics = []
    select = tree.find(exp.Select)
    for expression in select.expressions if select else []:
        value = expression.this if isinstance(expression, exp.Alias) else expression
        if not value.find(exp.AggFunc):
            continue
        metrics.append(
            {
                "name": expression.alias if isinstance(expression, exp.Alias) else "",
                "formula": value.sql(),
                "source_tables": list(shared["source_tables"]),
                "column_references": _column_references(
                    [value, *shared_expressions], tables, aliases
                ),
                "join_conditions": list(shared["join_conditions"]),
                "filters": list(shared["filters"]),
                "group_by": list(shared["group_by"]),
            }
        )
    return metrics


def _sql_tables_and_aliases(tree):
    cte_names = {cte.alias_or_name.casefold() for cte in tree.find_all(exp.CTE)}
    tables = []
    aliases = {}
    for table in tree.find_all(exp.Table):
        name = table.name
        if name.casefold() in cte_names:
            continue
        qualified = ".".join(part for part in (table.catalog, table.db, name) if part)
        if qualified not in tables:
            tables.append(qualified)
        aliases[table.alias_or_name.casefold()] = qualified
        aliases[name.casefold()] = qualified
    return tables, aliases


def _column_references(expressions, tables, aliases):
    columns = []
    seen_columns = set()
    for expression in expressions:
        for column in expression.find_all(exp.Column):
            qualifier = column.table
            if qualifier:
                table_name = aliases.get(qualifier.casefold(), qualifier)
            elif len(tables) == 1:
                table_name = tables[0]
            else:
                table_name = "<unqualified>"
            key = (table_name.casefold(), column.name.casefold())
            if key not in seen_columns:
                seen_columns.add(key)
                columns.append({"table_name": table_name, "column_name": column.name})
    return columns


def parse_dbt_manifest_metrics(manifest):
    """Read metrics from the supported dbt manifest v10-v11 semantic shape."""
    schema_url = str(manifest.get("metadata", {}).get("dbt_schema_version", ""))
    match = re.search(r"/manifest/v(\d+)\.json", schema_url)
    if not match or int(match.group(1)) not in (10, 11):
        return []
    metrics = manifest.get("metrics") or {}
    semantic_models = manifest.get("semantic_models") or {}
    if not metrics or not semantic_models:
        return []

    nodes = manifest.get("nodes") or {}
    measures = {}
    for semantic in semantic_models.values():
        model_nodes = semantic.get("depends_on", {}).get("nodes", [])
        node = next((nodes.get(node_id) for node_id in model_nodes if nodes.get(node_id)), {})
        sql = node.get("raw_code") or node.get("compiled_code") or ""
        parse_failed = not bool(sql)
        try:
            sql_facts = parse_sql_facts(sql) if sql else _empty_sql_facts()
        except Exception:
            compiled = node.get("compiled_code") or ""
            try:
                if not compiled or compiled == sql:
                    raise ValueError("No distinct compiled SQL is available")
                sql_facts = parse_sql_facts(compiled)
                parse_failed = False
            except Exception:
                sql_facts = _empty_sql_facts()
                parse_failed = True
        relation = node.get("relation_name") or node.get("alias") or node.get("name")
        if relation and not sql_facts["source_tables"]:
            sql_facts["source_tables"] = [relation]
        for measure in semantic.get("measures") or []:
            measures[measure.get("name")] = (measure, sql_facts, parse_failed)

    metric_by_name = {metric.get("name"): metric for metric in metrics.values()}
    result = []
    for metric in metrics.values():
        type_params = metric.get("type_params") or {}
        measure_refs = _measure_references(type_params)
        referenced_measures = [measures[name] for name in measure_refs if name in measures]
        fact_sets = [measure[1] for measure in referenced_measures]
        facts = _merge_sql_facts(fact_sets)
        sql_parse_failed = any(measure[2] for measure in referenced_measures)
        formula = "" if sql_parse_failed else _metric_formula(metric, metric_by_name, measures)
        result.append(
            {
                "name": metric.get("name", ""),
                "description": metric.get("description", ""),
                "formula": formula,
                "source_tables": list(facts["source_tables"]),
                "column_references": list(facts["column_references"]),
                "join_conditions": list(facts["join_conditions"]),
                "filters": list(facts["filters"]),
                "grain": ", ".join(facts["group_by"]),
                "business_rules": "",
                "trust_level": "dbt manifest v10-v11",
                "sql_parse_failed": sql_parse_failed,
            }
        )
    return result


def _metric_formula(metric, metric_by_name, measures, seen=None):
    seen = set(seen or ())
    name = metric.get("name", "")
    if name in seen:
        return ""
    seen.add(name)
    params = metric.get("type_params") or {}
    metric_type = metric.get("type", "")
    if metric_type == "derived" and params.get("expr"):
        return str(params["expr"])
    if metric_type == "ratio":
        numerator = _reference_name(params.get("numerator"))
        denominator = _reference_name(params.get("denominator"))
        return f"{numerator} / {denominator}" if numerator and denominator else ""
    measure_name = _reference_name(params.get("measure"))
    if measure_name in measures:
        measure = measures[measure_name][0]
        expression = measure.get("expr") or measure.get("name") or ""
        return _aggregate_formula(measure.get("agg"), expression)
    referenced = params.get("metrics") or []
    if referenced:
        return str(params.get("expr") or "")
    return ""


def _measure_references(params):
    references = []
    for key in ("measure", "numerator", "denominator", "base_measure", "conversion_measure"):
        name = _reference_name(params.get(key))
        if name:
            references.append(name)
    conversion = params.get("conversion_type_params") or {}
    for key in ("base_measure", "conversion_measure"):
        name = _reference_name(conversion.get(key))
        if name:
            references.append(name)
    return references


def _reference_name(reference):
    if isinstance(reference, str):
        return reference
    if isinstance(reference, dict):
        return reference.get("name") or reference.get("measure") or ""
    return ""


def _merge_sql_facts(fact_sets):
    merged = _empty_sql_facts()
    for facts in fact_sets:
        for key in merged:
            for value in facts[key]:
                if value not in merged[key]:
                    merged[key].append(value)
    return merged


def _aggregate_formula(aggregate, expression):
    if not aggregate or not expression:
        return expression
    aggregate = str(aggregate).casefold()
    if aggregate == "count_distinct":
        return f"COUNT(DISTINCT {expression})"
    return f"{aggregate.upper()}({expression})"


def _empty_sql_facts():
    return {
        "source_tables": [],
        "column_references": [],
        "formulas": [],
        "join_conditions": [],
        "filters": [],
        "group_by": [],
    }
