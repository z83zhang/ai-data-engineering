import re
from copy import deepcopy

from metric_import import parse_metric_definitions_yaml, render_metric_definitions_yaml


class RelationshipCorrectionError(ValueError):
    pass


def metric_measure_references(document, metric_name):
    """Return declared measures referenced by one metric."""
    actual_name = _mapping_name(document.get("metrics", {}), metric_name)
    if actual_name is None:
        raise KeyError(metric_name)
    metric = document["metrics"][actual_name]
    declared = [measure["name"] for measure in document.get("measures", [])]
    references = []
    for field in ("measure", "numerator", "denominator"):
        requested = metric.get(field)
        actual = next(
            (name for name in declared if name.casefold() == str(requested).casefold()),
            None,
        )
        if actual and actual not in references:
            references.append(actual)
    expression = str(metric.get("expression", ""))
    for name in declared:
        if name in references:
            continue
        if re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
            expression,
            re.IGNORECASE,
        ):
            references.append(name)
    return references


def shared_metric_dependencies(document, metric_name):
    """Map this metric's referenced measures to other metrics that use them."""
    target_name = _mapping_name(document.get("metrics", {}), metric_name)
    if target_name is None:
        raise KeyError(metric_name)
    shared = {}
    for measure_name in metric_measure_references(document, target_name):
        consumers = [
            other_name
            for other_name in document.get("metrics", {})
            if other_name.casefold() != target_name.casefold()
            and measure_name in metric_measure_references(document, other_name)
        ]
        if consumers:
            shared[measure_name] = consumers
    return shared


def metric_deletion_impact(document, metric_name):
    """Describe semantic facts uniquely owned by a metric versus shared ones."""
    references = metric_measure_references(document, metric_name)
    shared = shared_metric_dependencies(document, metric_name)
    removed_measures = [name for name in references if name not in shared]
    removed_measure_keys = {name.casefold() for name in removed_measures}
    remaining_measures = [
        measure
        for measure in document.get("measures", [])
        if measure["name"].casefold() not in removed_measure_keys
    ]
    candidate_entities = {
        measure["entity"].casefold(): measure["entity"]
        for measure in document.get("measures", [])
        if measure["name"].casefold() in removed_measure_keys
    }
    target_name = _mapping_name(document.get("metrics", {}), metric_name)
    remaining_metrics = {
        name: metric
        for name, metric in document.get("metrics", {}).items()
        if name.casefold() != target_name.casefold()
    }
    removed_entities = []
    for folded_entity, entity_name in candidate_entities.items():
        has_measure = any(
            str(measure.get("entity", "")).casefold() == folded_entity
            for measure in remaining_measures
        )
        has_dimension = any(
            str(dimension.get("entity", "")).casefold() == folded_entity
            for dimension in document.get("dimensions", [])
        )
        is_referenced = any(
            str(key.get("references_entity", "")).casefold() == folded_entity
            for owner, entity in document.get("entities", {}).items()
            if owner.casefold() != folded_entity
            for key in entity.get("keys", [])
        )
        has_filter = any(
            str(item.get("dimension", "")).casefold().startswith(
                f"{folded_entity}."
            )
            for metric in remaining_metrics.values()
            for item in metric.get("filters", [])
        )
        if not any((has_measure, has_dimension, is_referenced, has_filter)):
            removed_entities.append(entity_name)
    return {
        "shared_measures": shared,
        "removed_measures": removed_measures,
        "removed_entities": removed_entities,
    }


def delete_metric(document, metric_name):
    """Delete one metric, its notes, and only uniquely owned dependencies."""
    impact = metric_deletion_impact(document, metric_name)
    updated = deepcopy(document)
    actual_name = _mapping_name(updated.get("metrics", {}), metric_name)
    if actual_name is None:
        raise KeyError(metric_name)
    updated["metrics"].pop(actual_name)
    note_name = _mapping_name(updated.get("notes", {}), actual_name)
    if note_name is not None:
        updated["notes"].pop(note_name)
    removed_measure_keys = {
        name.casefold() for name in impact["removed_measures"]
    }
    updated["measures"] = [
        measure
        for measure in updated.get("measures", [])
        if measure["name"].casefold() not in removed_measure_keys
    ]
    removed_entity_keys = {name.casefold() for name in impact["removed_entities"]}
    updated["entities"] = {
        name: entity
        for name, entity in updated.get("entities", {}).items()
        if name.casefold() not in removed_entity_keys
    }
    return parse_metric_definitions_yaml(render_metric_definitions_yaml(updated))


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
