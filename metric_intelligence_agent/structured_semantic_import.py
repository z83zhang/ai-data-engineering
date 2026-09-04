import json
import re
from copy import deepcopy

from sqlglot import exp, parse_one

from metric_import import parse_metric_definitions_yaml, render_metric_definitions_yaml


class StructuredSemanticImportError(ValueError):
    pass


def extract_structured_semantic_facts(
    source_content, source_format, openai_client
):
    """Run the grounded extraction stage and return its structured facts."""
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": extraction_system_prompt()},
            {
                "role": "user",
                "content": (
                    f"Source format:\n{source_format}\n\n"
                    f"Structured file content:\n{source_content}"
                ),
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "structured_semantic_extraction",
                "strict": True,
                "schema": extraction_json_schema(),
            },
        },
        temperature=0,
    )
    return json.loads(response.choices[0].message.content)


def extraction_json_schema():
    """Return the strict response schema for grounded structured extraction."""
    text = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": text,
                        "source_table": text,
                        "keys": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "column": text,
                                    "type": {
                                        "type": "string",
                                        "enum": ["primary", "unique", "natural"],
                                    },
                                },
                                "required": ["column", "type"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["name", "source_table", "keys"],
                    "additionalProperties": False,
                },
            },
            "dimensions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": text,
                        "entity": text,
                        "column": text,
                        "type": {
                            "type": "string",
                            "enum": ["categorical", "time"],
                        },
                        "granularity": text,
                    },
                    "required": ["name", "entity", "column", "type", "granularity"],
                    "additionalProperties": False,
                },
            },
            "measures": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": text,
                        "entity": text,
                        "expression": text,
                        "aggregation": {
                            "type": "string",
                            "enum": [
                                "sum", "avg", "count", "count_distinct",
                                "min", "max", "custom",
                            ],
                        },
                        "re_aggregatable": {"type": "boolean"},
                    },
                    "required": [
                        "name", "entity", "expression", "aggregation",
                        "re_aggregatable",
                    ],
                    "additionalProperties": False,
                },
            },
            "metrics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": text,
                        "type": {
                            "type": "string",
                            "enum": ["simple", "ratio", "derived", "cumulative"],
                        },
                        "measure": text,
                        "numerator": text,
                        "denominator": text,
                        "expression": text,
                        "window": text,
                        "filters": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "dimension": text,
                                    "operator": text,
                                    "value": text,
                                },
                                "required": ["dimension", "operator", "value"],
                                "additionalProperties": False,
                            },
                        },
                        "grain": {"type": "array", "items": text},
                        "description": text,
                        "business_rules": text,
                        "caveats": text,
                        "ambiguity_rules": text,
                        "requires_metric_composition": {"type": "boolean"},
                        "composition_reason": text,
                    },
                    "required": [
                        "name", "type", "measure", "numerator", "denominator",
                        "expression", "window", "filters", "grain", "description",
                        "business_rules", "caveats", "ambiguity_rules",
                        "requires_metric_composition", "composition_reason",
                    ],
                    "additionalProperties": False,
                },
            },
            "relationships": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "left_entity": text,
                        "left_column": text,
                        "right_entity": text,
                        "right_column": text,
                        "direction": {
                            "type": "string",
                            "enum": [
                                "left_references_right",
                                "right_references_left",
                                "unresolved",
                            ],
                        },
                        "explicitly_declared": {"type": "boolean"},
                    },
                    "required": [
                        "left_entity", "left_column", "right_entity",
                        "right_column", "direction", "explicitly_declared",
                    ],
                    "additionalProperties": False,
                },
            },
            "warnings": {"type": "array", "items": text},
            "layer_suggestions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"table_name": text, "suggested_layer": text},
                    "required": ["table_name", "suggested_layer"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "entities", "dimensions", "measures", "metrics", "relationships",
            "warnings", "layer_suggestions",
        ],
        "additionalProperties": False,
    }


def extraction_system_prompt():
    return (
        "Extract an ADR-010 layered semantic document only from facts explicitly "
        "declared in the supplied structured source. Do not infer from names, query "
        "position, conventions, or training knowledge. Return entities, dimensions, "
        "measures, metrics, and relationship declarations. Names for dimensions and "
        "measures must be namespaced as entity.name. Filters must reference a declared "
        "dimension or entity key, never a raw column. Store filter values without SQL "
        "literal quotes. Every measure expression must be the complete aggregate SQL "
        "expression matching its aggregation, such as SUM(amount), not only the input "
        "column amount. Normalize LookML ${TABLE}.column references to the bare column "
        "name before returning the expression. Every independently resolvable measure "
        "that the source exposes for querying MUST produce both its namespaced measure "
        "record and a corresponding simple metric record that directly references that "
        "measure; this is required, not optional. Mechanically, every LookML measure: "
        "block and every entry under a Cube measures object is queryable and MUST produce "
        "that simple metric unless its definition requires metric-to-metric composition. "
        "For a composition-dependent entry, emit the flagged metric record but do not "
        "emit it as an independently resolvable measure. Do not leave metrics empty when "
        "an independently resolvable measure exists. Mark "
        "requires_metric_composition true when a "
        "metric depends on another metric rather than directly on independently "
        "resolvable measures; such metrics will be skipped. Do not fabricate a "
        "replacement expression. For relationships, set explicitly_declared true and "
        "supply direction only when the source format explicitly declares cardinality "
        "or ownership (for example LookML relationship or Cube joins). SQL equality "
        "alone does not establish direction; return unresolved. Never infer relationship "
        "direction from names or position. Apply this mechanical LookML rule: when a "
        "LookML explore join block contains the literal field relationship: many_to_one, "
        "always set explicitly_declared to true; use sql_on only to identify the two "
        "entity/column sides, then set direction so the explore/base-view side references "
        "the joined-view side. For relationship: one_to_many, always set "
        "explicitly_declared to true and set direction so the joined-view side references "
        "the explore/base-view side. Do not return explicitly_declared false for either "
        "of those two declared LookML values. If the LookML relationship field is absent, "
        "or its declared cardinality does not establish one foreign-key-owning side, use "
        "direction unresolved; explicitly_declared indicates only whether the field was "
        "present. Suggested layers are informational only."
    )


def build_structured_semantic_document(extraction, *, relationship_resolutions=None):
    """Normalize grounded LLM facts into the validated ADR-010 document."""
    payload = deepcopy(extraction)
    entities = {}
    warnings = list(payload.get("warnings") or [])
    for item in payload.get("entities") or []:
        name = _required(item, "name", "entity")
        source_table = _required(item, "source_table", f"entity {name!r}")
        entities[name] = {
            "source": {"type": "table", "value": source_table},
            "keys": [
                {"column": key["column"], "type": key["type"]}
                for key in item.get("keys") or []
            ],
        }

    relationships = []
    for item in payload.get("relationships") or []:
        candidate = _relationship_candidate(item)
        relationships.append((candidate, item))

    unresolved = []
    resolutions = relationship_resolutions or {}
    for candidate, source in relationships:
        direction = source.get("direction")
        if not source.get("explicitly_declared") or direction == "unresolved":
            direction = resolutions.get(candidate["id"])
        if direction in {"left_references_right", "right_references_left"}:
            _apply_relationship(entities, candidate, direction)
        else:
            unresolved.append(candidate)

    dimensions = []
    for item in payload.get("dimensions") or []:
        dimension = {
            "name": item["name"],
            "entity": item["entity"],
            "column": item["column"],
            "type": item["type"],
        }
        if item["type"] == "time":
            dimension["granularity"] = item["granularity"]
        dimensions.append(dimension)

    measures = deepcopy(payload.get("measures") or [])
    for measure in measures:
        measure["expression"] = _aggregate_measure_expression(
            measure.get("aggregation"), measure.get("expression")
        )
    metrics = {}
    notes = {}
    skipped_metric_names = set()
    for item in payload.get("metrics") or []:
        name = item.get("name") or "<unnamed>"
        if item.get("requires_metric_composition"):
            skipped_metric_names.add(name.casefold())
            reason = item.get("composition_reason") or "metric-to-metric composition"
            warnings.append(f"Skipped metric {name!r}: {reason}.")
            continue
        metric = {"type": item["type"]}
        field_by_type = {
            "simple": ("measure",),
            "ratio": ("numerator", "denominator"),
            "derived": ("expression",),
            "cumulative": ("measure", "window"),
        }
        for field in field_by_type[item["type"]]:
            if item.get(field):
                metric[field] = item[field]
        metric["filters"] = deepcopy(item.get("filters") or [])
        if item.get("grain"):
            metric["grain"] = list(item["grain"])
        metrics[name] = metric
        notes[name] = {
            "description": item.get("description") or "",
            "business_rules": item.get("business_rules") or "",
            "caveats": item.get("caveats") or "",
            "ambiguity_rules": item.get("ambiguity_rules") or "",
        }
    _promote_unrepresented_measures(
        measures, metrics, notes, skipped_metric_names
    )

    document = {
        "entities": entities,
        "dimensions": dimensions,
        "measures": measures,
        "metrics": metrics,
        "notes": notes,
    }
    _validate_metric_grain_references(document)
    document = parse_metric_definitions_yaml(render_metric_definitions_yaml(document))
    return {
        "document": document,
        "unresolved_keys": unresolved,
        "grounding": _grounding_references(
            document, [candidate for candidate, _ in relationships]
        ),
        "warnings": warnings,
        "layer_suggestions": deepcopy(payload.get("layer_suggestions") or []),
    }


def _relationship_candidate(item):
    left = {
        "entity": _required(item, "left_entity", "relationship"),
        "column": _required(item, "left_column", "relationship"),
    }
    right = {
        "entity": _required(item, "right_entity", "relationship"),
        "column": _required(item, "right_column", "relationship"),
    }
    sides = sorted(
        [f"{left['entity']}.{left['column']}", f"{right['entity']}.{right['column']}"],
        key=str.casefold,
    )
    return {
        "id": f"relationship:{sides[0]}={sides[1]}",
        "kind": "relationship",
        "left": left,
        "right": right,
    }


def _apply_relationship(entities, candidate, direction):
    if direction == "left_references_right":
        child, parent = candidate["left"], candidate["right"]
    elif direction == "right_references_left":
        child, parent = candidate["right"], candidate["left"]
    else:
        raise StructuredSemanticImportError(
            f"Invalid relationship resolution {direction!r}."
        )
    child_name = _entity_name(entities, child["entity"])
    parent_name = _entity_name(entities, parent["entity"])
    if child_name is None or parent_name is None:
        raise StructuredSemanticImportError(
            "Relationship references an entity absent from the extraction."
        )
    keys = entities[child_name]["keys"]
    incoming = {
        "column": child["column"],
        "type": "foreign",
        "references_entity": parent_name,
    }
    existing = next(
        (key for key in keys if key.get("column", "").casefold() == child["column"].casefold()),
        None,
    )
    if existing is not None and existing != incoming:
        raise StructuredSemanticImportError(
            f"Conflicting key declarations for {child['entity']}.{child['column']}."
        )
    if existing is None:
        keys.append(incoming)


def _grounding_references(document, unresolved):
    sources = {
        name: entity["source"]["value"]
        for name, entity in document["entities"].items()
        if entity["source"]["type"] == "table"
    }
    tables = list(dict.fromkeys(sources.values()))
    columns = []

    def add(entity_name, column):
        actual_name = _entity_name(sources, entity_name)
        table = sources.get(actual_name) if actual_name else None
        if table and column:
            reference = {"table_name": table, "column_name": str(column)}
            if reference not in columns:
                columns.append(reference)

    for entity_name, entity in document["entities"].items():
        for key in entity.get("keys") or []:
            add(entity_name, key.get("column"))
    for dimension in document["dimensions"]:
        add(dimension["entity"], dimension["column"])
    for measure in document["measures"]:
        for column in _expression_columns(measure["expression"]):
            add(measure["entity"], column)
    for candidate in unresolved:
        add(candidate["left"]["entity"], candidate["left"]["column"])
        add(candidate["right"]["entity"], candidate["right"]["column"])
    return {"source_tables": tables, "column_references": columns}


def _expression_columns(expression):
    try:
        tree = parse_one(expression)
    except Exception:
        return []
    return list(dict.fromkeys(column.name for column in tree.find_all(exp.Column)))


def _aggregate_measure_expression(aggregation, expression):
    value = str(expression or "").strip()
    value = re.sub(r"\$\{\s*TABLE\s*\}\.", "", value, flags=re.IGNORECASE)
    try:
        parsed = parse_one(value) if value else None
    except Exception:
        parsed = None
    if parsed is not None and any(parsed.find_all(exp.AggFunc)):
        return value
    if aggregation == "count" and not value:
        return "COUNT(*)"
    if not value:
        return value
    if aggregation == "count_distinct":
        return f"COUNT(DISTINCT {value})"
    if aggregation in {"sum", "avg", "count", "min", "max"}:
        return f"{aggregation.upper()}({value})"
    return value


def _promote_unrepresented_measures(measures, metrics, notes, skipped_names):
    represented = {
        metric.get("measure", "").casefold()
        for metric in metrics.values()
        if metric.get("type") == "simple"
    }
    metric_names = {name.casefold() for name in metrics}
    for measure in measures:
        measure_name = measure.get("name", "")
        if not measure_name or measure_name.casefold() in represented:
            continue
        metric_name = measure_name.rsplit(".", 1)[-1]
        if metric_name.casefold() in skipped_names:
            continue
        if metric_name.casefold() in metric_names:
            raise StructuredSemanticImportError(
                f"Cannot promote measure {measure_name!r}: metric name "
                f"{metric_name!r} is already used."
            )
        metrics[metric_name] = {
            "type": "simple",
            "measure": measure_name,
            "filters": [],
        }
        notes[metric_name] = {
            "description": "",
            "business_rules": "",
            "caveats": "",
            "ambiguity_rules": "",
        }
        metric_names.add(metric_name.casefold())


def _required(mapping, field, owner):
    value = mapping.get(field) if isinstance(mapping, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise StructuredSemanticImportError(
            f"{owner.capitalize()} requires non-empty {field!r}."
        )
    return value


def _validate_metric_grain_references(document):
    allowed = {item["name"].casefold() for item in document["dimensions"]}
    allowed.update(
        f"{entity_name}.{key['column']}".casefold()
        for entity_name, entity in document["entities"].items()
        for key in entity.get("keys") or []
    )
    for metric_name, metric in document["metrics"].items():
        for grain in metric.get("grain") or []:
            if not isinstance(grain, str) or grain.casefold() not in allowed:
                raise StructuredSemanticImportError(
                    f"Metric {metric_name!r} grain references undeclared "
                    f"dimension or entity key {grain!r}."
                )


def _entity_name(entities, requested):
    folded = str(requested).casefold()
    return next((name for name in entities if name.casefold() == folded), None)
