import json
import re
import shutil
from datetime import datetime
from pathlib import Path

import streamlit as st

from agent import client
from connectors.duckdb import DuckDBConnector
from connectors.sqlite import SQLiteConnector
from dbt_semantic_import import extract_dbt_semantic_document
from grounding import schema_grounding_issues
from metric_import import (
    RelationshipConflictError,
    empty_metric_document,
    load_metric_definitions_yaml,
    merge_metric_documents,
    parse_metric_definitions_yaml,
    preserve_analyst_notes,
    protected_note_reimports,
    render_metric_definitions_yaml,
    save_metric_definitions_yaml,
    update_metric_notes,
)
from manual_semantic_review import (
    correct_relationship,
    derived_relationships,
    grouped_entities,
    grouped_metrics,
    metric_facts,
    relationship_label,
)
from sql_semantic_import import (
    apply_confirmed_entity_keys,
    extract_sql_semantic_document,
)
from structured_semantic_import import (
    build_structured_semantic_document,
    extract_structured_semantic_facts,
)


CONNECTION_TYPES = [
    "DuckDB file (.db, .duckdb)",
    "SQLite file (.sqlite, .db)",
    "PostgreSQL — coming soon",
    "Snowflake — coming soon",
    "BigQuery — coming soon",
]

COMING_SOON_TYPES = CONNECTION_TYPES[2:]

def _table_count(schema_df):
    columns = ["table_name"]
    if "table_schema" in schema_df.columns:
        columns.insert(0, "table_schema")
    return len(schema_df[columns].drop_duplicates())


def _tab1_complete():
    custom_context = Path(__file__).resolve().parents[1] / "custom_context"
    return (
        (custom_context / "schema.sql").is_file()
        and (custom_context / "table_catalog.md").is_file()
    )


def _tab2_complete():
    custom_context = Path(__file__).resolve().parents[1] / "custom_context"
    return any(
        (custom_context / filename).is_file()
        for filename in ("metric_definitions.yaml", "metric_definitions.md")
    )


def _validate_catalog(text, schema_df):
    if len(text.strip()) < 100:
        return False, "Catalog seems too short. Generate again."
    table_names = schema_df["table_name"].unique().tolist()
    missing = [
        table
        for table in table_names
        if not re.search(
            r"\b" + re.escape(table) + r"\b",
            text,
            re.IGNORECASE,
        )
    ]
    if len(missing) > len(table_names) * 0.5:
        return False, f"Catalog may be missing tables: {missing[:3]}"
    return True, None


def _save_catalog(catalog_path, content):
    if catalog_path.is_file():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = catalog_path.with_suffix(f".{timestamp}.bak")
        backup.write_text(
            catalog_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    catalog_path.write_text(content, encoding="utf-8")


def _clean_catalog_response(content):
    return content.replace("```markdown", "").replace("```", "").strip()


def _metric_prose(facts, name_hint, trust_level):
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "Write only business-facing prose for the supplied deterministic "
                    "SQL facts. Do not add or change tables, columns, formulas, joins, "
                    "filters, or grouping facts. Use an empty string when unsupported."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"name_hint": name_hint, "trust_level": trust_level, "facts": facts},
                    indent=2,
                ),
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "metric_prose",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "grain": {"type": "string"},
                        "business_rules": {"type": "string"},
                    },
                    "required": ["name", "description", "grain", "business_rules"],
                    "additionalProperties": False,
                },
            },
        },
        temperature=0,
    )
    return json.loads(response.choices[0].message.content)


def _documents_for_metric_save(metric_path, reviewed_yaml, confirmed_document):
    incoming = parse_metric_definitions_yaml(reviewed_yaml)
    incoming = apply_confirmed_entity_keys(incoming, confirmed_document)
    existing = (
        load_metric_definitions_yaml(metric_path)
        if metric_path.is_file()
        else empty_metric_document()
    )
    return existing, incoming


def _save_sql_metric_document(
    metric_path,
    reviewed_yaml,
    confirmed_document,
    *,
    confirm_relationship_conflicts=False,
):
    existing, incoming = _documents_for_metric_save(
        metric_path, reviewed_yaml, confirmed_document
    )
    merged = merge_metric_documents(
        existing,
        incoming,
        confirm_relationship_conflicts=confirm_relationship_conflicts,
    )
    for metric_name, notes in incoming["notes"].items():
        merged = update_metric_notes(merged, metric_name, **notes)
    if metric_path.is_file():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(metric_path, metric_path.with_suffix(f".{timestamp}.bak"))
    save_metric_definitions_yaml(metric_path, merged)


def _save_manual_metric_document(metric_path, document):
    if metric_path.is_file():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(metric_path, metric_path.with_suffix(f".{timestamp}.bak"))
    save_metric_definitions_yaml(metric_path, document)


def _manual_saved_message():
    message = st.session_state.pop("manual_review_saved", None)
    if message:
        st.success(message)


def _clear_manual_relationship_widget_state():
    for key in list(st.session_state):
        if key.startswith("manual_relationship_"):
            st.session_state.pop(key, None)


def _render_metric_note_editor(metric_path, document, metric_name):
    notes = document.get("notes", {}).get(metric_name, {})
    facts = metric_facts(document, metric_name)
    with st.expander(metric_name):
        with st.form(f"manual_notes_{metric_name}"):
            facts_column, notes_column = st.columns(2)
            with facts_column:
                st.caption("Parsed facts (read-only)")
                st.json(facts)
            with notes_column:
                st.caption("Analyst notes")
                description = st.text_area(
                    "Description", value=notes.get("description", "")
                )
                business_rules = st.text_area(
                    "Business rules", value=notes.get("business_rules", "")
                )
                caveats = st.text_area("Caveats", value=notes.get("caveats", ""))
                ambiguity_rules = st.text_area(
                    "Ambiguity rules", value=notes.get("ambiguity_rules", "")
                )
            if st.form_submit_button("Save notes"):
                latest = load_metric_definitions_yaml(metric_path)
                updated = update_metric_notes(
                    latest,
                    metric_name,
                    description=description,
                    business_rules=business_rules,
                    caveats=caveats,
                    ambiguity_rules=ambiguity_rules,
                )
                _save_manual_metric_document(metric_path, updated)
                st.session_state.manual_review_saved = f"Saved notes for {metric_name}."
                st.rerun()


def _relationship_changed(relationship, owner, column, key_type, reference):
    return any(
        (
            relationship["from_entity"].casefold() != owner.casefold(),
            relationship["column"].casefold() != column.strip().casefold(),
            relationship["key_type"] != key_type,
            key_type == "foreign"
            and relationship["references_entity"].casefold() != reference.casefold(),
        )
    )


def _render_relationship_editor(metric_path, document, relationship, index):
    entity_names = list(document["entities"])
    current_owner = next(
        name
        for name in entity_names
        if name.casefold() == relationship["from_entity"].casefold()
    )
    current_reference = next(
        name
        for name in entity_names
        if name.casefold() == relationship["references_entity"].casefold()
    )
    title = relationship_label(relationship)
    with st.expander(title):
        owner = st.selectbox(
            "Key-owning entity",
            entity_names,
            index=entity_names.index(current_owner),
            key=f"manual_relationship_owner_{index}",
        )
        column = st.text_input(
            "Key column",
            value=relationship["column"],
            key=f"manual_relationship_column_{index}",
        )
        key_types = ["foreign", "primary", "unique", "natural"]
        key_type = st.selectbox(
            "Key type", key_types, index=0, key=f"manual_relationship_type_{index}"
        )
        reference = ""
        if key_type == "foreign":
            reference = st.selectbox(
                "References entity",
                entity_names,
                index=entity_names.index(current_reference),
                key=f"manual_relationship_reference_{index}",
            )
        changed = _relationship_changed(
            relationship, owner, column, key_type, reference
        )
        acknowledged = st.checkbox(
            "I reviewed this relationship correction and want to save it.",
            key=f"manual_relationship_ack_{index}",
        )
        if st.button(
            "Save relationship correction",
            key=f"manual_relationship_save_{index}",
            disabled=not changed or not acknowledged or not column.strip(),
        ):
            try:
                latest = load_metric_definitions_yaml(metric_path)
                updated = correct_relationship(
                    latest,
                    relationship,
                    from_entity=owner,
                    column=column,
                    key_type=key_type,
                    references_entity=reference or None,
                )
                _save_manual_metric_document(metric_path, updated)
                st.session_state.manual_review_saved = "Saved relationship correction."
                _clear_manual_relationship_widget_state()
                st.rerun()
            except ValueError as error:
                st.error(str(error))


def _render_manual_review(metric_path):
    st.caption(
        "Review imported semantic definitions, add analyst-owned notes, and "
        "correct saved relationships."
    )
    if not metric_path.is_file():
        st.info("Import semantic definitions before reviewing them here.")
        return
    _manual_saved_message()
    document = load_metric_definitions_yaml(metric_path)

    st.subheader("Metric notes")
    if not document["metrics"]:
        st.info("No metrics are currently available to annotate.")
    for metric_name in document["metrics"]:
        _render_metric_note_editor(metric_path, document, metric_name)

    st.subheader("Relationships")
    relationships = derived_relationships(document)
    if not relationships:
        st.info("No saved foreign-key relationships are available to review.")
    for index, relationship in enumerate(relationships):
        _render_relationship_editor(metric_path, document, relationship, index)


def _render_named_records(records, fields):
    if not records:
        st.caption("None declared")
        return
    for record in records:
        details = " · ".join(
            f"{label}: `{record[field]}`"
            for field, label in fields
            if record.get(field) not in (None, "", [])
        )
        st.markdown(f"- **{record['name']}**" + (f" — {details}" if details else ""))


def _render_grouped_entity(entity):
    with st.expander(entity["name"]):
        source = entity["source"]
        st.markdown(
            f"**Source:** {source.get('type', 'unknown')} · "
            f"`{source.get('value', '')}`"
        )

        st.markdown("**Keys**")
        if entity["keys"]:
            for key in entity["keys"]:
                st.markdown(f"- `{key['column']}` — {key['type']}")
        else:
            st.caption("None declared")

        st.markdown("**Relationships**")
        if entity["relationships"]:
            for relationship in entity["relationships"]:
                st.markdown(f"- {relationship_label(relationship)}")
        else:
            st.caption("None declared")

        st.markdown("**Dimensions**")
        _render_named_records(
            entity["dimensions"],
            (("column", "column"), ("type", "type")),
        )

        st.markdown("**Measures**")
        _render_named_records(
            entity["measures"],
            (
                ("expression", "expression"),
                ("aggregation", "aggregation"),
                ("re_aggregatable", "re-aggregatable"),
            ),
        )


def _render_grouped_metric(metric):
    facts = metric["facts"]
    notes = metric["notes"]
    with st.expander(metric["name"]):
        st.markdown(f"**Type:** {facts['type']}")
        for measure in metric["measures"]:
            st.markdown(
                f"**Measure:** {measure['name']} · "
                f"expression: `{measure['expression']}`"
            )
        if facts.get("expression"):
            st.markdown(f"**Expression:** `{facts['expression']}`")
        if facts.get("window"):
            st.markdown(f"**Window:** `{facts['window']}`")

        st.markdown("**Filters:**")
        if facts.get("filters"):
            for item in facts["filters"]:
                st.markdown(
                    f"- `{item['dimension']}` {item['operator']} `{item['value']}`"
                )
        else:
            st.caption("None")
        grain = facts.get("grain") or []
        st.markdown(
            "**Grain:** " + (", ".join(f"`{item}`" for item in grain) or "None")
        )

        st.markdown("**Analyst notes**")
        note_labels = (
            ("description", "Description"),
            ("business_rules", "Business rules"),
            ("caveats", "Caveats"),
            ("ambiguity_rules", "Ambiguity rules"),
        )
        for field, label in note_labels:
            st.markdown(f"- **{label}:** {notes.get(field) or '—'}")


def _render_current_metric_definitions(metric_path, legacy_metric_path):
    st.divider()
    st.subheader("Current Metric Definitions")
    if not metric_path.is_file():
        st.markdown(legacy_metric_path.read_text(encoding="utf-8"))
        return

    document = load_metric_definitions_yaml(metric_path)
    st.markdown("#### Entities")
    for entity in grouped_entities(document):
        _render_grouped_entity(entity)

    st.markdown("#### Metrics")
    if not document["metrics"]:
        st.caption("No metrics declared")
    for metric in grouped_metrics(document):
        _render_grouped_metric(metric)

    with st.expander("View raw YAML", expanded=False):
        st.code(metric_path.read_text(encoding="utf-8"), language="yaml")


def _relationship_conflict_signature(reviewed_yaml, confirmed_document):
    return json.dumps(
        {
            "reviewed_yaml": reviewed_yaml,
            "confirmed_entities": confirmed_document.get("entities", {}),
        },
        sort_keys=True,
    )


def _relationship_conflict_acknowledgment(
    state_prefix, reviewed_yaml, confirmed_document
):
    conflicts_key = f"{state_prefix}_relationship_conflicts"
    signature_key = f"{state_prefix}_relationship_conflict_signature"
    acknowledgment_key = f"{state_prefix}_relationship_conflict_ack"
    signature = _relationship_conflict_signature(reviewed_yaml, confirmed_document)
    if st.session_state.get(signature_key) != signature:
        st.session_state.pop(conflicts_key, None)
        st.session_state.pop(signature_key, None)
        st.session_state.pop(acknowledgment_key, None)
    conflicts = st.session_state.get(conflicts_key, [])
    if not conflicts:
        return False

    st.warning(
        "This import changes existing relationship declarations. Review and "
        "confirm these changes before saving."
    )
    for conflict in conflicts:
        existing = conflict.get("existing")
        incoming = conflict.get("incoming")
        st.write(
            f"- **{conflict['entity']}.{conflict['column']}**: "
            f"`{existing}` → `{incoming if incoming is not None else 'removed'}`"
        )
    return st.checkbox(
        "I reviewed these relationship changes and want to apply them.",
        key=acknowledgment_key,
    )


def _record_relationship_conflicts(
    state_prefix, reviewed_yaml, confirmed_document, conflicts
):
    st.session_state[f"{state_prefix}_relationship_conflicts"] = conflicts
    st.session_state[f"{state_prefix}_relationship_conflict_signature"] = (
        _relationship_conflict_signature(reviewed_yaml, confirmed_document)
    )
    st.session_state.pop(f"{state_prefix}_relationship_conflict_ack", None)


def _clear_relationship_conflict_state(state_prefix):
    for suffix in (
        "relationship_conflicts",
        "relationship_conflict_signature",
        "relationship_conflict_ack",
    ):
        st.session_state.pop(f"{state_prefix}_{suffix}", None)


def _preserve_saved_notes(document, metric_path):
    if not metric_path.is_file():
        return document
    existing = load_metric_definitions_yaml(metric_path)
    return preserve_analyst_notes(document, existing)


def _show_analyst_notes_reimport_notice(document, metric_path):
    if not metric_path.is_file():
        return
    existing = load_metric_definitions_yaml(metric_path)
    protected = protected_note_reimports(document, existing)
    for metric_name in protected:
        st.info(
            f"{metric_name} has analyst-authored notes, which will not be changed "
            "by this import. If the underlying logic changed meaningfully, review "
            "the notes on the Manual tab."
        )


def _render_dbt_layered_import(import_result, custom_context):
    unknown_tables = import_result.get("unknown_tables", [])
    unknown_columns = import_result.get("unknown_columns", [])
    for warning in import_result.get("warnings", []):
        st.warning(warning)
    if unknown_tables:
        st.warning(f"Source tables not found in the connected schema: {unknown_tables}")
    if unknown_columns:
        st.warning(f"Source columns not found in the connected schema: {unknown_columns}")

    resolutions = {}
    unresolved_keys = import_result.get("unresolved_keys", [])
    if unresolved_keys:
        st.warning(
            "Some dbt relationship declarations do not state a complete direction. "
            "Confirm each before saving."
        )
    for candidate in unresolved_keys:
        left = candidate["left"]
        right = candidate["right"]
        options = {
            "Select relationship direction": None,
            f"{left['entity']}.{left['column']} references {right['entity']}.{right['column']}": "left_references_right",
            f"{right['entity']}.{right['column']} references {left['entity']}.{left['column']}": "right_references_left",
        }
        label = st.selectbox(
            f"dbt relationship: {left['entity']}.{left['column']} = {right['entity']}.{right['column']}",
            list(options),
            key=f"dbt_resolution_{candidate['id']}",
        )
        if options[label]:
            resolutions[candidate["id"]] = options[label]

    resolved = extract_dbt_semantic_document(
        import_result["manifest"], relationship_resolutions=resolutions
    )
    resolved["document"]["notes"] = import_result["notes"]
    yaml_path = custom_context / "metric_definitions.yaml"
    _show_analyst_notes_reimport_notice(resolved["document"], yaml_path)
    resolved["document"] = _preserve_saved_notes(resolved["document"], yaml_path)
    signature = json.dumps(resolutions, sort_keys=True)
    if st.session_state.get("dbt_resolution_signature") != signature:
        st.session_state.dbt_resolution_signature = signature
        st.session_state.pop("dbt_metric_review", None)

    if not resolved["document"]["metrics"]:
        st.info(
            "No manifest metrics were present; model entities, dimensions, "
            "measures, and relationships can still be saved."
        )
    st.subheader("Extracted dbt Semantic Definitions")
    dbt_review = st.text_area(
        "Review dbt definitions",
        value=render_metric_definitions_yaml(resolved["document"]),
        height=500,
        key="dbt_metric_review",
    )

    has_grounding_issues = bool(unknown_tables or unknown_columns)
    grounding_acknowledged = not has_grounding_issues
    if has_grounding_issues:
        grounding_acknowledged = st.checkbox(
            "I acknowledge these references are not present in the discovered "
            "schema and want to save anyway.",
            key="structured_import_grounding_ack",
        )
    all_relationships_resolved = not resolved["unresolved_keys"]
    conflict_acknowledged = _relationship_conflict_acknowledgment(
        "dbt_import", dbt_review, resolved["document"]
    )
    has_pending_conflicts = bool(
        st.session_state.get("dbt_import_relationship_conflicts")
    )
    save_col, discard_col = st.columns(2)
    with save_col:
        if st.button(
            "Save Imported Definitions",
            disabled=(
                not grounding_acknowledged
                or not all_relationships_resolved
                or (has_pending_conflicts and not conflict_acknowledged)
            ),
        ):
            try:
                _save_sql_metric_document(
                    yaml_path,
                    dbt_review,
                    resolved["document"],
                    confirm_relationship_conflicts=(
                        has_pending_conflicts and conflict_acknowledged
                    ),
                )
                _clear_relationship_conflict_state("dbt_import")
                st.session_state.pop("structured_import_result", None)
                st.session_state.pop("dbt_resolution_signature", None)
                st.success("✅ dbt definitions saved")
                st.rerun()
            except RelationshipConflictError as error:
                _record_relationship_conflicts(
                    "dbt_import",
                    dbt_review,
                    resolved["document"],
                    error.conflicts,
                )
                st.rerun()
            except Exception as error:
                st.error(f"Failed to save dbt import: {error}")
    with discard_col:
        if st.button("Discard Import"):
            st.session_state.pop("structured_import_result", None)
            st.session_state.pop("structured_import_grounding_ack", None)
            st.session_state.pop("dbt_resolution_signature", None)
            _clear_relationship_conflict_state("dbt_import")
            st.rerun()


def _render_other_layered_import(import_result, custom_context):
    resolutions = {}
    unresolved_keys = import_result.get("unresolved_keys", [])
    if unresolved_keys:
        st.warning(
            "Some relationships do not have a source-declared direction. "
            "Confirm each before saving."
        )
    for candidate in unresolved_keys:
        left = candidate["left"]
        right = candidate["right"]
        options = {
            "Select relationship direction": None,
            f"{left['entity']}.{left['column']} references {right['entity']}.{right['column']}": "left_references_right",
            f"{right['entity']}.{right['column']} references {left['entity']}.{left['column']}": "right_references_left",
        }
        label = st.selectbox(
            f"Relationship: {left['entity']}.{left['column']} = {right['entity']}.{right['column']}",
            list(options),
            key=f"structured_resolution_{candidate['id']}",
        )
        if options[label]:
            resolutions[candidate["id"]] = options[label]

    resolved = build_structured_semantic_document(
        import_result["extraction"], relationship_resolutions=resolutions
    )
    yaml_path = custom_context / "metric_definitions.yaml"
    _show_analyst_notes_reimport_notice(resolved["document"], yaml_path)
    resolved["document"] = _preserve_saved_notes(resolved["document"], yaml_path)
    signature = json.dumps(resolutions, sort_keys=True)
    if st.session_state.get("structured_resolution_signature") != signature:
        st.session_state.structured_resolution_signature = signature
        st.session_state.pop("structured_metric_review", None)

    for warning in resolved["warnings"]:
        st.warning(warning)
    unknown_tables = import_result.get("unknown_tables", [])
    unknown_columns = import_result.get("unknown_columns", [])
    if unknown_tables:
        st.warning(
            "Source tables not found in the connected schema: "
            f"{unknown_tables}"
        )
    if unknown_columns:
        st.warning(
            "Source columns not found in the connected schema: "
            f"{unknown_columns}"
        )

    st.subheader("Extracted Semantic Definitions")
    metric_review = st.text_area(
        "Review structured definitions",
        value=render_metric_definitions_yaml(resolved["document"]),
        height=500,
        key="structured_metric_review",
    )

    if resolved.get("layer_suggestions"):
        suggestions = ", ".join(
            f"{item['table_name']}: {item['suggested_layer']}"
            for item in resolved["layer_suggestions"]
        )
        st.info(
            "Source-provided layer suggestions (informational only; not saved): "
            f"{suggestions}"
        )

    has_grounding_issues = bool(unknown_tables or unknown_columns)
    grounding_acknowledged = not has_grounding_issues
    if has_grounding_issues:
        grounding_acknowledged = st.checkbox(
            "I acknowledge these references are not present in the discovered "
            "schema and want to save anyway.",
            key="structured_import_grounding_ack",
        )
    all_relationships_resolved = not resolved["unresolved_keys"]
    conflict_acknowledged = _relationship_conflict_acknowledgment(
        "structured_import", metric_review, resolved["document"]
    )
    has_pending_conflicts = bool(
        st.session_state.get("structured_import_relationship_conflicts")
    )
    save_col, discard_col = st.columns(2)
    with save_col:
        if st.button(
            "Save Imported Definitions",
            disabled=(
                not grounding_acknowledged
                or not all_relationships_resolved
                or (has_pending_conflicts and not conflict_acknowledged)
            ),
        ):
            try:
                _save_sql_metric_document(
                    yaml_path,
                    metric_review,
                    resolved["document"],
                    confirm_relationship_conflicts=(
                        has_pending_conflicts and conflict_acknowledged
                    ),
                )
                _clear_relationship_conflict_state("structured_import")
                st.session_state.pop("structured_import_result", None)
                st.session_state.pop("structured_resolution_signature", None)
                st.success("âœ… Structured definitions saved")
                st.rerun()
            except RelationshipConflictError as error:
                _record_relationship_conflicts(
                    "structured_import",
                    metric_review,
                    resolved["document"],
                    error.conflicts,
                )
                st.rerun()
            except Exception as error:
                st.error(f"Failed to save structured import: {error}")
    with discard_col:
        if st.button("Discard Import"):
            st.session_state.pop("structured_import_result", None)
            st.session_state.pop("structured_import_grounding_ack", None)
            st.session_state.pop("structured_resolution_signature", None)
            _clear_relationship_conflict_state("structured_import")
            st.rerun()


def _show_table_catalog(custom_context):
    st.divider()
    st.subheader("Step 2a: Classify your tables")
    st.caption(
        "Layer classification cannot be reliably inferred from table names "
        "alone. Please classify each table — this determines which tables "
        "the agent prefers for different question types."
    )

    schema_df = st.session_state.get("schema_df")
    if schema_df is None:
        schema_df = st.session_state.get("raw_schema_df")
    tables = (
        sorted(schema_df["table_name"].unique().tolist())
        if schema_df is not None
        else []
    )
    layers_path = custom_context / "layer_classifications.json"
    saved_layers = {}
    if layers_path.is_file():
        saved_layers = json.loads(layers_path.read_text(encoding="utf-8"))

    layer_classifications = {}
    optional_context = ""
    if not tables:
        st.info("Connect a database above to classify your tables.")
    else:
        layer_options = [
            "Dimension",
            "Fact",
            "Aggregated",
            "Bridge",
            "Skip",
        ]
        for table in tables:
            col1, col2 = st.columns([3, 2])
            with col1:
                st.write(f"**{table}**")
            with col2:
                default_layer = saved_layers.get(table, "Dimension")
                layer = st.selectbox(
                    "Layer",
                    layer_options,
                    index=(
                        layer_options.index(default_layer)
                        if default_layer in layer_options
                        else 0
                    ),
                    key=f"layer_{table}",
                    label_visibility="collapsed",
                )
            layer_classifications[table] = layer

        optional_context = st.text_area(
            "Additional context (optional)",
            placeholder=(
                "Paste any tribal knowledge about your data: naming "
                "conventions, business rules not obvious from column names, "
                "tables to avoid, known caveats."
            ),
            height=80,
            key="optional_context_input",
        )
    st.session_state.layer_classifications = layer_classifications
    layers_path.write_text(
        json.dumps(layer_classifications, indent=2),
        encoding="utf-8",
    )

    st.divider()
    st.subheader("Step 2b: Generate Table Catalog")

    catalog_path = custom_context / "table_catalog.md"
    existing_catalog = (
        catalog_path.read_text(encoding="utf-8")
        if catalog_path.is_file()
        else None
    )
    st.session_state.setdefault("catalog_draft", existing_catalog or "")

    button_label = (
        "Regenerate"
        if st.session_state.catalog_draft.strip()
        else "Generate Table Catalog"
    )
    if st.button(button_label):
        try:
            with st.spinner("Analysing schema and generating table catalog..."):
                project_root = Path(__file__).resolve().parents[1]
                demo_catalog = (
                    project_root / "context" / "table_catalog.md"
                ).read_text(encoding="utf-8")
                schema_sql = (custom_context / "schema.sql").read_text(
                    encoding="utf-8"
                )
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a data engineering assistant generating "
                                "a table_catalog.md.\n\n"
                                "The analyst has classified each table — use "
                                "these classifications as ground truth, do not "
                                "override them.\n\n"
                                "For each table generate:\n"
                                "- Layer: use exactly what the analyst provided\n"
                                "- Grain: what one row represents\n"
                                "- Key columns with obvious business meaning\n"
                                "- Filter flag columns (Status, IsActive, "
                                "Discontinued, etc.)\n"
                                "- Join paths: always add this placeholder:\n"
                                "  '- Join paths: ⚠️ Import trusted SQL in "
                                "Metric Definitions tab to define accurate join "
                                "paths'\n\n"
                                "Do not infer join paths from column names. Do "
                                "not add business rules not evident from the "
                                "schema. Use the format template for structure "
                                "only. Return only markdown, no code fences."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                "Analyst layer classifications:\n"
                                f"{json.dumps(layer_classifications, indent=2)}"
                                "\n\nAdditional context from analyst:\n"
                                f"{optional_context or 'None provided'}\n\n"
                                f"Schema:\n{schema_sql}\n\n"
                                "Format template (structure only):\n"
                                f"{demo_catalog}"
                            ),
                        },
                    ],
                    temperature=0,
                )
                generated_catalog = _clean_catalog_response(
                    response.choices[0].message.content.strip()
                )
                st.session_state.catalog_previous = (
                    st.session_state.catalog_draft
                )
                st.session_state.catalog_draft = generated_catalog
                st.session_state.pop("catalog_save_warning", None)
        except Exception as error:
            st.error(f"Failed to generate table catalog: {error}")

    st.markdown(st.session_state.catalog_draft)

    with st.expander("🔍 Optional: Enhance with documentation"):
        st.caption(
            "Trusted SQL is the most valuable input — join paths are extracted "
            "from actual JOIN conditions, not guessed. Wiki pages and README "
            "files also help."
        )
        additional_context = st.text_area(
            "Paste documentation or trusted SQL",
            height=200,
            placeholder=(
                "Most valuable (in order):\n"
                "1. Trusted SQL from dashboards or pipelines\n"
                "   → join paths extracted from JOIN conditions\n"
                "2. dbt model SQL or descriptions\n"
                "3. Wiki pages or README files\n"
                "4. ERD descriptions"
            ),
            key="catalog_enrich_input",
        )
        if st.button("Enhance Catalog") and additional_context.strip():
            try:
                with st.spinner("Enhancing catalog..."):
                    schema_sql = (custom_context / "schema.sql").read_text(
                        encoding="utf-8"
                    )
                    response = client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "Update this table catalog using the provided "
                                    "documentation. If trusted SQL is provided, "
                                    "extract join paths from actual JOIN "
                                    "conditions and replace ⚠️ placeholders with "
                                    "verified paths. Preserve all existing content "
                                    "not contradicted. Use the schema as source of "
                                    "truth for table and column name validation. "
                                    "Return complete updated markdown only, no "
                                    "code fences."
                                ),
                            },
                            {
                                "role": "user",
                                "content": (
                                    "Current catalog:\n"
                                    f"{st.session_state.catalog_draft}\n\n"
                                    "Documentation or trusted SQL:\n"
                                    f"{additional_context}\n\n"
                                    f"Schema for reference:\n{schema_sql}"
                                ),
                            },
                        ],
                        temperature=0,
                    )
                    updated = _clean_catalog_response(
                        response.choices[0].message.content.strip()
                    )
                    st.session_state.catalog_previous = (
                        st.session_state.catalog_draft
                    )
                    st.session_state.catalog_draft = updated
                    st.session_state.pop("catalog_save_warning", None)
                    st.rerun()
            except Exception as error:
                st.error(f"Failed to enhance table catalog: {error}")

    if (
        st.session_state.get("catalog_previous")
        and st.session_state.catalog_previous
        != st.session_state.catalog_draft
    ):
        if st.button("↩ Undo last change"):
            st.session_state.catalog_draft = (
                st.session_state.catalog_previous
            )
            st.session_state.pop("catalog_previous", None)
            st.session_state.pop("catalog_save_warning", None)
            st.rerun()

    st.caption(
        "Suggested refinements: "
        "'Mark [table] as [aggregated/fact/dimension]' · "
        "'Add note that [column] = [value] means [meaning]' · "
        "'Flag [table] as a bridge table to exclude from direct queries'"
    )
    with st.form("catalog_refinement_form", clear_on_submit=True):
        refinement = st.text_input(
            "Refine table catalog",
            placeholder="Describe the change you want",
        )
        submitted = st.form_submit_button(
            "Update Table Catalog",
            use_container_width=True,
        )
    if submitted and refinement.strip():
        try:
            with st.spinner("Updating table catalog..."):
                schema_sql = (custom_context / "schema.sql").read_text(
                    encoding="utf-8"
                )
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a data engineering assistant. "
                                "Revise the table catalog according to the "
                                "requested change. Use the provided schema "
                                "as the source of truth — do not reference "
                                "tables or columns that do not exist in the "
                                "schema. Preserve all existing content unless "
                                "the instruction explicitly changes it. "
                                "Return the complete updated catalog as "
                                "markdown only, no code fences."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Schema:\n{schema_sql}\n\n"
                                "Current table catalog:\n"
                                f"{st.session_state.catalog_draft}\n\n"
                                f"Requested change:\n{refinement.strip()}"
                            ),
                        },
                    ],
                    temperature=0,
                )
                updated_catalog = _clean_catalog_response(
                    response.choices[0].message.content.strip()
                )
                st.session_state.catalog_previous = (
                    st.session_state.catalog_draft
                )
                st.session_state.catalog_draft = updated_catalog
                st.session_state.pop("catalog_save_warning", None)
        except Exception as error:
            st.error(f"Failed to update table catalog: {error}")

    with st.expander("Edit manually"):
        manual_edit = st.text_area(
            "Edit table catalog",
            value=st.session_state.catalog_draft,
            height=400,
        )
        if st.button("Apply manual edits"):
            st.session_state.catalog_previous = (
                st.session_state.catalog_draft
            )
            st.session_state.catalog_draft = manual_edit
            st.session_state.pop("catalog_save_warning", None)
            st.rerun()

    if st.button("Save Table Catalog"):
        valid, error = _validate_catalog(
            st.session_state.catalog_draft,
            st.session_state.schema_df,
        )
        if valid:
            _save_catalog(catalog_path, st.session_state.catalog_draft)
            st.session_state.pop("catalog_save_warning", None)
            st.success("✅ Table catalog saved")
            st.rerun()
        else:
            st.session_state.catalog_save_warning = error

    if st.session_state.get("catalog_save_warning"):
        st.warning(
            f"⚠️ {st.session_state.catalog_save_warning} — save anyway?"
        )
        if st.button("Save anyway"):
            _save_catalog(catalog_path, st.session_state.catalog_draft)
            st.session_state.pop("catalog_save_warning", None)
            st.success("✅ Table catalog saved")
            st.rerun()


def show():
    custom_context = Path(__file__).resolve().parents[1] / "custom_context"
    layers_path = custom_context / "layer_classifications.json"
    st.title("Setup Mode")
    st.info(f"Active query source: {st.session_state.data_source}")
    setup_source_path = st.session_state.get("setup_source_path")
    if setup_source_path and st.session_state.get("custom_conn") is not None:
        setup_path = Path(setup_source_path)
        project_root = Path(__file__).resolve().parents[1]
        try:
            setup_display = setup_path.relative_to(project_root)
        except ValueError:
            setup_display = Path(setup_path.name)
        setup_is_active = (
            st.session_state.get("conn") is st.session_state.get("custom_conn")
        )
        setup_state = "active query source" if setup_is_active else "not yet activated"
        st.info(f"Setup connected to: {setup_display.as_posix()} ({setup_state})")
    else:
        st.info("Setup connected to: none")
    connection_tab, metric_tab, validate_tab = st.tabs(
        [
            "🔌 Data Source",
            "📋 Metric Definitions",
            "✅ Validate",
        ]
    )

    with connection_tab:
        if st.session_state.data_source == "custom":
            st.warning("Switching to demo will clear conversation history")
            if st.checkbox("I understand"):
                if st.button("Switch to TPC-H demo"):
                    from agent import get_date_range
                    from app import get_demo_database, switch_data_source

                    conn = get_demo_database()
                    min_date, max_date = get_date_range(conn)
                    switch_data_source(
                        conn,
                        min_date,
                        max_date,
                        context_dir=None,
                        data_source="demo",
                    )
                    st.rerun()

        connection_type = st.selectbox("Connection type", CONNECTION_TYPES)
        schema_is_prepared = False

        if connection_type in COMING_SOON_TYPES:
            st.info(
                "This connector is planned. See README for the connector "
                "pattern to contribute one."
            )
        else:
            file_path = st.text_input(
                "File path",
                help=(
                    "Use an absolute path e.g. /Users/name/data/mydb.db or a "
                    "path relative to where you launched the app"
                ),
            )
            if st.button("Connect (read-only)", type="primary"):
                if not file_path.strip():
                    st.error("Enter a database file path.")
                else:
                    try:
                        connector_class = (
                            DuckDBConnector
                            if connection_type.startswith("DuckDB")
                            else SQLiteConnector
                        )
                        connector = connector_class()
                        custom_conn = connector.connect(
                            {"path": file_path.strip()}
                        )
                        if not connector.test_connection():
                            raise ConnectionError("Connection test failed.")

                        schema_df = connector.discover_schema()
                        schemas = []
                        if "table_schema" in schema_df.columns:
                            schemas = schema_df["table_schema"].unique().tolist()

                        st.session_state.discovered_schemas = schemas
                        st.session_state.raw_schema_df = schema_df
                        st.session_state.custom_conn = custom_conn
                        st.session_state.connector = connector
                        st.session_state.setup_source_path = str(
                            Path(file_path.strip()).resolve()
                        )
                        st.session_state.setup_connection_type = connection_type
                        st.session_state.pop("prepared_schema", None)
                        st.session_state.pop("confirmed_schema", None)
                        st.session_state.pop("catalog_draft", None)
                        st.session_state.pop("catalog_previous", None)
                        st.session_state.pop("catalog_refinement", None)
                        st.session_state.pop("catalog_save_warning", None)
                        st.rerun()
                    except Exception as error:
                        st.error(str(error))

            if "raw_schema_df" in st.session_state:
                raw_schema_df = st.session_state.raw_schema_df
                selected_schema = None
                if st.session_state.get("discovered_schemas"):
                    selected_schema = st.selectbox(
                        "Select schema",
                        st.session_state.discovered_schemas,
                    )
                    schema_df = raw_schema_df[
                        raw_schema_df["table_schema"] == selected_schema
                    ].reset_index(drop=True)
                    if st.button("Confirm schema selection"):
                        st.session_state.confirmed_schema = selected_schema
                else:
                    schema_df = raw_schema_df

                schema_key = selected_schema or "__single_schema__"
                schema_confirmed = (
                    not st.session_state.get("discovered_schemas")
                    or st.session_state.get("confirmed_schema") == selected_schema
                )
                if (
                    schema_confirmed
                    and st.session_state.get("prepared_schema") != schema_key
                ):
                    try:
                        with st.spinner("Generating schema SQL..."):
                            schema_sql = (
                                st.session_state.connector.generate_schema_sql(
                                    schema_df
                                )
                            )
                        context_dir = Path(__file__).resolve().parents[1] / (
                            "custom_context"
                        )
                        context_dir.mkdir(parents=True, exist_ok=True)
                        (context_dir / "schema.sql").write_text(
                            schema_sql,
                            encoding="utf-8",
                        )
                        st.session_state.schema_df = schema_df
                        st.session_state.table_count = _table_count(schema_df)
                        st.session_state.prepared_schema = schema_key
                    except Exception as error:
                        st.error(str(error))

                schema_is_prepared = (
                    schema_confirmed
                    and st.session_state.get("prepared_schema") == schema_key
                    and (custom_context / "schema.sql").is_file()
                )

        if schema_is_prepared and not _tab1_complete():
            schema_df = st.session_state.schema_df
            tables = sorted(schema_df["table_name"].unique().tolist())
            st.write(f"**{len(tables)} tables discovered:**")
            for table_name in tables:
                table_cols = schema_df[
                    schema_df["table_name"] == table_name
                ][["column_name", "data_type"]]
                with st.expander(
                    f"📋 {table_name} ({len(table_cols)} columns)"
                ):
                    st.dataframe(
                        table_cols,
                        use_container_width=True,
                        hide_index=True,
                    )
            st.success(
                "Connected read-only and saved schema "
                f"for {st.session_state.table_count} tables."
            )
            _show_table_catalog(custom_context)
        elif schema_is_prepared:
            _show_table_catalog(custom_context)

        if _tab1_complete():
            st.success(
                "✅ Custom context found — schema.sql and table_catalog.md "
                "loaded from previous session"
            )
            col1, col2 = st.columns([3, 1])
            with col1:
                st.info("→ Proceed to Metric Definitions tab")
            with col2:
                if st.button("🗑️ Clear context"):
                    layers_path.unlink(missing_ok=True)
                    shutil.rmtree(custom_context, ignore_errors=True)
                    for key in [
                        "catalog_draft",
                        "catalog_previous",
                        "schema_df",
                        "raw_schema_df",
                        "discovered_schemas",
                        "prepared_schema",
                        "confirmed_schema",
                        "table_count",
                        "layer_classifications",
                        "custom_conn",
                        "connector",
                        "setup_source_path",
                        "setup_connection_type",
                    ]:
                        st.session_state.pop(key, None)
                    st.rerun()

    with metric_tab:
        if not _tab1_complete():
            st.warning(
                "Complete the Data Source tab first — "
                "schema.sql and table_catalog.md must be saved."
            )
            st.stop()

        yaml_metric_path = custom_context / "metric_definitions.yaml"
        legacy_metric_path = custom_context / "metric_definitions.md"
        if yaml_metric_path.is_file():
            st.success("✅ metric_definitions.yaml saved")
        else:
            st.info(
                "No metric definitions yet. "
                "Import from one of the sources below."
            )

        source_tab1, source_tab2, source_tab3 = st.tabs(
            [
                "📦 Structured Files",
                "🔄 Pipeline & Dashboard SQL",
                "✏️ Manual",
            ]
        )
        with source_tab1:
            structured_format = st.radio(
                "Format",
                [
                    "dbt manifest.json / semantic_manifest.json",
                    "Other structured format",
                ],
                key="structured_import_format",
            )
            if structured_format.startswith("dbt manifest"):
                st.caption(
                    "Deterministic import for dbt manifest schema v10-v11. "
                    "Other dbt artifact shapes are not interpreted."
                )
            else:
                st.caption(
                    "Flexible LLM extraction with schema grounding for LookML, "
                    "Cube, and other structured formats."
                )
            input_method = st.radio(
                "How would you like to provide the file?",
                ["Enter file path", "Upload file"],
                horizontal=True,
            )

            if input_method == "Enter file path":
                file_path_input = st.text_input(
                    "File path",
                    placeholder="e.g. jaffle_shop_duckdb/target/manifest.json",
                )
                file_content = None
                if file_path_input.strip():
                    try:
                        file_content = Path(file_path_input.strip()).read_text(
                            encoding="utf-8"
                        )
                    except Exception as error:
                        st.error(f"Could not read file: {error}")
            else:
                uploaded = st.file_uploader(
                    "Upload structured definition file",
                    type=["json", "yml", "yaml", "js", "lkml"],
                    key="structured_file_upload",
                )
                file_content = (
                    uploaded.read().decode("utf-8") if uploaded else None
                )

            if file_content is not None:
                if st.button("Import Structured File"):
                    try:
                        st.session_state.pop(
                            "structured_import_grounding_ack",
                            None,
                        )
                        if structured_format.startswith("dbt manifest"):
                            manifest = json.loads(file_content)
                            extraction = extract_dbt_semantic_document(manifest)
                            if extraction is None:
                                st.warning(
                                    "Only dbt manifest schema versions v10-v11 are supported."
                                )
                                st.stop()
                            document = extraction["document"]
                            if not document["entities"]:
                                st.warning("No model entities found in this dbt manifest.")
                                st.stop()
                            schema_df = st.session_state.get("schema_df")
                            if schema_df is None:
                                schema_df = st.session_state.get("raw_schema_df")
                            unknown_tables, unknown_columns = schema_grounding_issues(
                                [extraction["grounding"]], schema_df
                            )
                            st.session_state.structured_import_result = {
                                "kind": "dbt_layered",
                                "manifest": manifest,
                                "notes": document["notes"],
                                "warnings": extraction["warnings"],
                                "unresolved_keys": extraction["unresolved_keys"],
                                "unknown_tables": unknown_tables,
                                "unknown_columns": unknown_columns,
                            }
                            st.rerun()
                        else:
                            source_content = file_content

                        with st.spinner("Extracting metric definitions..."):
                            extraction = extract_structured_semantic_facts(
                                source_content,
                                structured_format,
                                client,
                            )
                        layered = build_structured_semantic_document(extraction)
                        if not layered["document"]["entities"]:
                            st.warning(
                                "No semantic entities found in this file. Choose a "
                                "file that explicitly declares semantic definitions."
                            )
                            st.stop()

                        schema_df = st.session_state.get("schema_df")
                        if schema_df is None:
                            schema_df = st.session_state.get("raw_schema_df")
                        unknown_tables, unknown_columns = schema_grounding_issues(
                            [layered["grounding"]], schema_df
                        )
                        st.session_state.structured_import_result = {
                            "kind": "other_layered",
                            "extraction": extraction,
                            "unresolved_keys": layered["unresolved_keys"],
                            "unknown_tables": unknown_tables,
                            "unknown_columns": unknown_columns,
                        }
                        st.rerun()
                    except Exception as error:
                        st.error(f"Failed to import structured file: {error}")

            if st.session_state.get("structured_import_result"):
                import_result = st.session_state.structured_import_result
                if import_result.get("kind") == "dbt_layered":
                    _render_dbt_layered_import(import_result, custom_context)
                    st.stop()
                if import_result.get("kind") == "other_layered":
                    _render_other_layered_import(import_result, custom_context)
                    st.stop()
        with source_tab2:
            sql_metric_path = custom_context / "metric_definitions.yaml"
            if st.session_state.pop("sql_import_saved", False):
                st.session_state.pop("metric_sql_input", None)
                st.session_state.pop("sql_metric_name_hint", None)
                st.session_state.pop("sql_metric_review", None)
                st.success("Saved. Paste another query to import more metrics.")
            metric_name_hint = st.text_input(
                "Metric name (optional)", key="sql_metric_name_hint"
            )
            sql_trust = st.selectbox(
                "SQL trust level",
                ["Trusted production SQL", "Analyst-provided SQL", "Needs review"],
            )
            pasted_sql = st.text_area(
                "Pipeline or dashboard SQL", height=260, key="metric_sql_input"
            )
            if st.button("Import SQL", disabled=not pasted_sql.strip()):
                try:
                    st.session_state.pop("sql_import_grounding_ack", None)
                    st.session_state.pop("sql_key_resolutions", None)
                    extraction = extract_sql_semantic_document(
                        pasted_sql, metric_name_hint.strip()
                    )
                    metrics = extraction["document"]["metrics"]
                    with st.spinner("Writing metric descriptions..."):
                        for name, metric in metrics.items():
                            prose_facts = {
                                "name": name,
                                "metric": metric,
                                "entities": extraction["document"]["entities"],
                                "measures": extraction["document"]["measures"],
                            }
                            prose = _metric_prose(prose_facts, name, sql_trust)
                            extraction["document"]["notes"][name] = {
                                "description": prose["description"],
                                "business_rules": prose["business_rules"],
                                "caveats": "",
                                "ambiguity_rules": "",
                                "provenance": "system_generated",
                            }
                    schema_df = st.session_state.get("schema_df")
                    if schema_df is None:
                        schema_df = st.session_state.get("raw_schema_df")
                    unknown_tables, unknown_columns = schema_grounding_issues(
                        [extraction["grounding"]], schema_df
                    )
                    st.session_state.sql_import_result = {
                        "sql": pasted_sql,
                        "metric_name_hint": metric_name_hint.strip(),
                        "notes": extraction["document"]["notes"],
                        "metric_count": extraction["metric_count"],
                        "unresolved_keys": extraction["unresolved_keys"],
                        "unknown_tables": unknown_tables,
                        "unknown_columns": unknown_columns,
                    }
                    st.session_state.pop("sql_metric_review", None)
                except Exception as error:
                    st.error(f"Failed to import SQL: {error}")

            if st.session_state.get("sql_import_result"):
                sql_result = st.session_state.sql_import_result
                unknown_tables = sql_result["unknown_tables"]
                unknown_columns = sql_result["unknown_columns"]
                if sql_result.get("metric_count", 1) > 1:
                    st.info(
                        f"{sql_result['metric_count']} metrics detected — using "
                        "column aliases as names; edit below if needed."
                    )
                if unknown_tables:
                    st.warning(
                        "Source tables not found in the connected schema: "
                        f"{unknown_tables}"
                    )
                if unknown_columns:
                    st.warning(
                        "Source columns not found in the connected schema: "
                        f"{unknown_columns}"
                    )
                resolutions = {}
                unresolved_keys = sql_result.get("unresolved_keys", [])
                if unresolved_keys:
                    st.warning(
                        "SQL establishes key usage but not every key classification "
                        "or relationship direction. Confirm each item before saving."
                    )
                for candidate in unresolved_keys:
                    if candidate["kind"] == "relationship":
                        left = candidate["left"]
                        right = candidate["right"]
                        options = {
                            "Select relationship direction": None,
                            f"{left['entity']}.{left['column']} references {right['entity']}.{right['column']}": "left_references_right",
                            f"{right['entity']}.{right['column']} references {left['entity']}.{left['column']}": "right_references_left",
                        }
                        label = st.selectbox(
                            f"Relationship: {left['entity']}.{left['column']} = {right['entity']}.{right['column']}",
                            list(options),
                            key=f"sql_resolution_{candidate['id']}",
                        )
                        if options[label]:
                            resolutions[candidate["id"]] = options[label]
                    else:
                        options = ["Select key type", *candidate["choices"]]
                        selection = st.selectbox(
                            f"Key classification: {candidate['entity']}.{candidate['column']}",
                            options,
                            key=f"sql_resolution_{candidate['id']}",
                        )
                        if selection != "Select key type":
                            resolutions[candidate["id"]] = selection

                resolved = extract_sql_semantic_document(
                    sql_result["sql"],
                    sql_result["metric_name_hint"],
                    key_resolutions=resolutions,
                )
                resolved["document"]["notes"] = sql_result["notes"]
                _show_analyst_notes_reimport_notice(
                    resolved["document"], sql_metric_path
                )
                resolved["document"] = _preserve_saved_notes(
                    resolved["document"], sql_metric_path
                )
                resolution_signature = json.dumps(resolutions, sort_keys=True)
                if st.session_state.get("sql_resolution_signature") != resolution_signature:
                    st.session_state.sql_resolution_signature = resolution_signature
                    st.session_state.pop("sql_metric_review", None)
                st.subheader("Extracted Metric Definition")
                sql_review = st.text_area(
                    "Review metric definition",
                    value=render_metric_definitions_yaml(resolved["document"]),
                    height=400,
                    key="sql_metric_review",
                )
                has_issues = bool(unknown_tables or unknown_columns)
                acknowledged = not has_issues
                if has_issues:
                    acknowledged = st.checkbox(
                        "I acknowledge these references are not present in the "
                        "discovered schema and want to save anyway.",
                        key="sql_import_grounding_ack",
                    )
                all_keys_resolved = not resolved["unresolved_keys"]
                conflict_acknowledged = _relationship_conflict_acknowledgment(
                    "sql_import", sql_review, resolved["document"]
                )
                has_pending_conflicts = bool(
                    st.session_state.get("sql_import_relationship_conflicts")
                )
                save_col, discard_col = st.columns(2)
                with save_col:
                    if st.button(
                        "Save SQL Metric",
                        disabled=(
                            not acknowledged
                            or not all_keys_resolved
                            or (has_pending_conflicts and not conflict_acknowledged)
                        ),
                    ):
                        try:
                            _save_sql_metric_document(
                                sql_metric_path,
                                sql_review,
                                resolved["document"],
                                confirm_relationship_conflicts=(
                                    has_pending_conflicts and conflict_acknowledged
                                ),
                            )
                            _clear_relationship_conflict_state("sql_import")
                            st.session_state.pop("sql_import_result", None)
                            st.session_state.pop("sql_resolution_signature", None)
                            st.session_state.sql_import_saved = True
                            st.rerun()
                        except RelationshipConflictError as error:
                            _record_relationship_conflicts(
                                "sql_import",
                                sql_review,
                                resolved["document"],
                                error.conflicts,
                            )
                            st.rerun()
                        except Exception as error:
                            st.error(f"Failed to save SQL import: {error}")
                with discard_col:
                    if st.button("Discard SQL Import"):
                        st.session_state.pop("sql_import_result", None)
                        st.session_state.pop("sql_import_grounding_ack", None)
                        st.session_state.pop("sql_resolution_signature", None)
                        _clear_relationship_conflict_state("sql_import")
                        st.rerun()
        with source_tab3:
            _render_manual_review(yaml_metric_path)

        sql_metric_path = custom_context / "metric_definitions.yaml"
        if legacy_metric_path.is_file() or sql_metric_path.is_file():
            _render_current_metric_definitions(sql_metric_path, legacy_metric_path)

    with validate_tab:
        st.info(
            "Complete Data Source and Metric Definitions tabs before validating."
        )
