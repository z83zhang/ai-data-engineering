from copy import deepcopy

from metric_import import parse_metric_definitions_yaml, render_metric_definitions_yaml


class RelationshipCorrectionError(ValueError):
    pass


def metric_facts(document, metric_name):
    """Return one metric's parsed facts without its separately owned notes."""
    actual_name = _mapping_name(document.get("metrics", {}), metric_name)
    if actual_name is None:
        raise KeyError(metric_name)
    return deepcopy(document["metrics"][actual_name])


def derived_relationships(document):
    """Materialize ADR-010 relationships from entity foreign keys."""
    relationships = []
    for entity_name, entity in document.get("entities", {}).items():
        for key in entity.get("keys") or []:
            if key.get("type") != "foreign":
                continue
            relationships.append(
                {
                    "from_entity": entity_name,
                    "column": key["column"],
                    "references_entity": key["references_entity"],
                    "key_type": key["type"],
                }
            )
    return sorted(
        relationships,
        key=lambda item: (
            item["from_entity"].casefold(),
            item["column"].casefold(),
            item["references_entity"].casefold(),
        ),
    )


def relationship_label(relationship):
    """Return the shared analyst-facing relationship representation."""
    return (
        f"{relationship['from_entity']}.{relationship['column']} → "
        f"{relationship['references_entity']}"
    )


def grouped_entities(document):
    """Group entity-owned facts for read-only presentation."""
    relationships = derived_relationships(document)
    groups = []
    for entity_name, entity in document.get("entities", {}).items():
        folded_name = entity_name.casefold()
        groups.append(
            {
                "name": entity_name,
                "source": deepcopy(entity.get("source", {})),
                "keys": [
                    deepcopy(key)
                    for key in entity.get("keys") or []
                    if key.get("type") != "foreign"
                ],
                "relationships": [
                    deepcopy(relationship)
                    for relationship in relationships
                    if relationship["from_entity"].casefold() == folded_name
                ],
                "dimensions": [
                    deepcopy(dimension)
                    for dimension in document.get("dimensions", [])
                    if str(dimension.get("entity", "")).casefold() == folded_name
                ],
                "measures": [
                    deepcopy(measure)
                    for measure in document.get("measures", [])
                    if str(measure.get("entity", "")).casefold() == folded_name
                ],
            }
        )
    return groups


def grouped_metrics(document):
    """Co-locate metric facts, referenced measures, and analyst notes."""
    measures = {
        measure["name"].casefold(): measure
        for measure in document.get("measures", [])
    }
    notes = {
        name.casefold(): value for name, value in document.get("notes", {}).items()
    }
    groups = []
    for metric_name, metric in document.get("metrics", {}).items():
        references = []
        for field in ("measure", "numerator", "denominator"):
            reference = metric.get(field)
            if isinstance(reference, str) and reference.casefold() in measures:
                references.append(deepcopy(measures[reference.casefold()]))
        groups.append(
            {
                "name": metric_name,
                "facts": deepcopy(metric),
                "measures": references,
                "notes": deepcopy(notes.get(metric_name.casefold(), {})),
            }
        )
    return groups


def correct_relationship(
    document,
    relationship,
    *,
    from_entity,
    column,
    key_type,
    references_entity=None,
):
    """Apply one explicitly acknowledged relationship/key correction."""
    updated = deepcopy(document)
    entities = updated.get("entities", {})
    old_entity_name = _mapping_name(entities, relationship["from_entity"])
    if old_entity_name is None:
        raise RelationshipCorrectionError("The original relationship entity is absent.")
    old_keys = entities[old_entity_name].get("keys") or []
    old_index = next(
        (
            index
            for index, key in enumerate(old_keys)
            if key.get("type") == "foreign"
            and str(key.get("column", "")).casefold()
            == str(relationship["column"]).casefold()
            and str(key.get("references_entity", "")).casefold()
            == str(relationship["references_entity"]).casefold()
        ),
        None,
    )
    if old_index is None:
        raise RelationshipCorrectionError(
            "The relationship changed since this editor was rendered. Reload and retry."
        )
    old_keys.pop(old_index)

    target_name = _mapping_name(entities, from_entity)
    if target_name is None:
        raise RelationshipCorrectionError(f"Unknown entity {from_entity!r}.")
    column = str(column or "").strip()
    if not column:
        raise RelationshipCorrectionError("A key column is required.")
    if key_type not in {"primary", "foreign", "unique", "natural"}:
        raise RelationshipCorrectionError(f"Unsupported key type {key_type!r}.")

    replacement = {"column": column, "type": key_type}
    if key_type == "foreign":
        reference_name = _mapping_name(entities, references_entity)
        if reference_name is None:
            raise RelationshipCorrectionError(
                f"Unknown referenced entity {references_entity!r}."
            )
        replacement["references_entity"] = reference_name

    target_keys = entities[target_name].setdefault("keys", [])
    target_keys[:] = [
        key
        for key in target_keys
        if str(key.get("column", "")).casefold() != column.casefold()
    ]
    target_keys.append(replacement)
    target_keys.sort(key=lambda key: (key["type"], key["column"].casefold()))
    return parse_metric_definitions_yaml(render_metric_definitions_yaml(updated))


def _mapping_name(mapping, requested):
    folded = str(requested or "").casefold()
    return next((name for name in mapping if name.casefold() == folded), None)
