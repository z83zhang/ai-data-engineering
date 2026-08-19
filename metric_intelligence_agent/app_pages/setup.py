import json
import re
import shutil
from datetime import datetime
from pathlib import Path

import streamlit as st

from agent import client
from connectors.duckdb import DuckDBConnector
from connectors.sqlite import SQLiteConnector


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
    return (custom_context / "metric_definitions.md").is_file()


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


def _filter_dbt_json(content):
    parsed = json.loads(content)
    model_fields = {
        "name",
        "alias",
        "database",
        "schema",
        "relation_name",
        "description",
        "columns",
        "config",
        "depends_on",
        "refs",
        "sources",
        "raw_code",
        "compiled_code",
    }
    models = {}
    for node_id, node in parsed.get("nodes", {}).items():
        if node.get("resource_type") != "model":
            continue
        model = {key: node[key] for key in model_fields if key in node}
        config = model.get("config")
        if isinstance(config, dict):
            model["config"] = {
                key: config[key]
                for key in ("materialized", "unique_key", "enabled")
                if key in config
            }
        if model.get("raw_code"):
            model.pop("compiled_code", None)
        models[node_id] = model

    filtered = {"nodes": models}
    for key in ("metrics", "semantic_models", "sources"):
        if key in parsed:
            filtered[key] = parsed[key]
    return json.dumps(filtered, indent=2, default=str)


def _markdown_sections(text):
    headings = list(re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", text))
    sections = {}
    for index, heading in enumerate(headings):
        level = len(heading.group(1))
        end = len(text)
        for next_heading in headings[index + 1 :]:
            if len(next_heading.group(1)) <= level:
                end = next_heading.start()
                break
        title = heading.group(2).strip().strip("`").casefold()
        sections[title] = (heading.start(), end, text[heading.start() : end].strip())
    return sections


def _merge_table_catalog_updates(existing_catalog, updates, table_names):
    existing_sections = _markdown_sections(existing_catalog)
    update_sections = _markdown_sections(updates)
    replacements = []
    additions = []

    for table_name in table_names:
        key = str(table_name).casefold()
        update = update_sections.get(key)
        if update is None:
            continue
        existing = existing_sections.get(key)
        if existing is None:
            additions.append(update[2])
        else:
            replacements.append((existing[0], existing[1], update[2]))

    merged = existing_catalog
    for start, end, replacement in sorted(replacements, reverse=True):
        merged = merged[:start] + replacement + "\n\n" + merged[end:].lstrip()
    if additions:
        merged = merged.rstrip() + "\n\n" + "\n\n".join(additions)
    return merged.strip() + "\n"


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
    st.info(f"Current data source: {st.session_state.data_source}")
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
                        st.session_state.pop("prepared_schema", None)
                        st.session_state.pop("confirmed_schema", None)
                        st.session_state.pop("catalog_draft", None)
                        st.session_state.pop("catalog_previous", None)
                        st.session_state.pop("catalog_refinement", None)
                        st.session_state.pop("catalog_save_warning", None)
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

        metric_path = custom_context / "metric_definitions.md"
        if metric_path.is_file():
            st.success("✅ metric_definitions.md saved")
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
                    "dbt metrics.yml / schema.yml",
                    "Looker LookML",
                    "Cube schema",
                ],
                key="structured_import_format",
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
                        if structured_format.startswith("dbt manifest"):
                            source_content = _filter_dbt_json(file_content)
                        else:
                            source_content = file_content

                        with st.spinner("Extracting metric definitions..."):
                            extraction_response = client.chat.completions.create(
                                model="gpt-4o",
                                messages=[
                                    {
                                        "role": "system",
                                        "content": (
                                            "Extract ONLY what is explicitly defined "
                                            "in the provided file. Do not infer, "
                                            "generate, or use knowledge from your "
                                            "training data. If no metrics exist in "
                                            "the file, return an empty metrics array. "
                                            "Each field must be populated from the "
                                            "file content only — use empty string if "
                                            "not found."
                                        ),
                                    },
                                    {
                                        "role": "user",
                                        "content": (
                                            f"Source format:\n{structured_format}\n\n"
                                            "Structured file content:\n"
                                            f"{source_content}"
                                        ),
                                    },
                                ],
                                response_format={
                                    "type": "json_schema",
                                    "json_schema": {
                                        "name": "structured_metric_extraction",
                                        "strict": True,
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "metrics": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "name": {"type": "string"},
                                                            "description": {
                                                                "type": "string"
                                                            },
                                                            "formula": {
                                                                "type": "string"
                                                            },
                                                            "source_tables": {
                                                                "type": "array",
                                                                "items": {
                                                                    "type": "string"
                                                                },
                                                            },
                                                            "join_conditions": {
                                                                "type": "array",
                                                                "items": {
                                                                    "type": "string"
                                                                },
                                                            },
                                                            "filters": {
                                                                "type": "array",
                                                                "items": {
                                                                    "type": "string"
                                                                },
                                                            },
                                                            "grain": {
                                                                "type": "string"
                                                            },
                                                            "trust_level": {
                                                                "type": "string"
                                                            },
                                                        },
                                                        "required": [
                                                            "name",
                                                            "description",
                                                            "formula",
                                                            "source_tables",
                                                            "join_conditions",
                                                            "filters",
                                                            "grain",
                                                            "trust_level",
                                                        ],
                                                        "additionalProperties": False,
                                                    },
                                                },
                                                "table_updates": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "table_name": {
                                                                "type": "string"
                                                            },
                                                            "layer": {
                                                                "type": "string"
                                                            },
                                                            "grain": {
                                                                "type": "string"
                                                            },
                                                            "notes": {
                                                                "type": "string"
                                                            },
                                                        },
                                                        "required": [
                                                            "table_name",
                                                            "layer",
                                                            "grain",
                                                            "notes",
                                                        ],
                                                        "additionalProperties": False,
                                                    },
                                                },
                                            },
                                            "required": ["metrics", "table_updates"],
                                            "additionalProperties": False,
                                        },
                                    },
                                },
                                temperature=0,
                            )
                        extraction = json.loads(
                            extraction_response.choices[0].message.content
                        )
                        metrics = extraction["metrics"]
                        if not metrics:
                            st.warning(
                                "No metric definitions found in this file. "
                                "This manifest has dbt models but no formal "
                                "metric definitions. Try the Pipeline & "
                                "Dashboard SQL tab and paste your model SQL "
                                "directly."
                            )
                            st.stop()

                        schema_df = st.session_state.get("schema_df")
                        if schema_df is None:
                            schema_df = st.session_state.get("raw_schema_df")
                        schema_tables = set()
                        if schema_df is not None:
                            schema_tables.update(
                                str(table).casefold()
                                for table in schema_df["table_name"].unique()
                            )
                            if "table_schema" in schema_df.columns:
                                schema_tables.update(
                                    (
                                        f"{row['table_schema']}."
                                        f"{row['table_name']}"
                                    ).casefold()
                                    for _, row in schema_df[
                                        ["table_schema", "table_name"]
                                    ]
                                    .drop_duplicates()
                                    .iterrows()
                                )
                        unknown_tables = sorted(
                            {
                                table
                                for metric in metrics
                                for table in metric["source_tables"]
                                if table.casefold() not in schema_tables
                            }
                        )
                        if unknown_tables:
                            st.warning(
                                "Potential hallucination: source tables not "
                                f"found in the connected schema: {unknown_tables}"
                            )

                        project_root = Path(__file__).resolve().parents[1]
                        demo_metrics = (
                            project_root / "context" / "metric_definitions.md"
                        ).read_text(encoding="utf-8")
                        with st.spinner("Formatting metric definitions..."):
                            markdown_response = client.chat.completions.create(
                                model="gpt-4o",
                                messages=[
                                    {
                                        "role": "system",
                                        "content": (
                                            "Convert the provided structured "
                                            "metric extraction into markdown. Use "
                                            "the template for structure only and "
                                            "do not add, infer, or alter facts. "
                                            "Return only complete markdown with no "
                                            "code fences."
                                        ),
                                    },
                                    {
                                        "role": "user",
                                        "content": (
                                            "Structured extraction:\n"
                                            f"{json.dumps(metrics, indent=2)}\n\n"
                                            "Markdown structure template:\n"
                                            f"{demo_metrics}"
                                        ),
                                    },
                                ],
                                temperature=0,
                            )
                        metric_definitions = _clean_catalog_response(
                            markdown_response.choices[0].message.content.strip()
                        )

                        table_sections = []
                        for update in extraction["table_updates"]:
                            lines = [f"### `{update['table_name']}`"]
                            if update["layer"]:
                                lines.append(f"- Layer: {update['layer']}")
                            if update["grain"]:
                                lines.append(f"- Grain: {update['grain']}")
                            if update["notes"]:
                                lines.append(f"- Notes: {update['notes']}")
                            table_sections.append("\n".join(lines))
                        table_catalog_updates = "\n\n".join(table_sections)

                        st.session_state.structured_import_result = {
                            "metric_definitions": metric_definitions,
                            "table_catalog_updates": table_catalog_updates,
                        }
                    except Exception as error:
                        st.error(f"Failed to import structured file: {error}")

            if st.session_state.get("structured_import_result"):
                import_result = st.session_state.structured_import_result
                st.subheader("Extracted Metric Definitions")
                st.markdown(import_result["metric_definitions"])
                with st.expander("Edit manually"):
                    metrics_review = st.text_area(
                        "Edit metric definitions",
                        value=import_result["metric_definitions"],
                        height=400,
                    )

                st.subheader("Table Catalog Updates")
                st.markdown(import_result["table_catalog_updates"])
                with st.expander("Edit manually"):
                    catalog_updates_review = st.text_area(
                        "Edit table catalog updates",
                        value=import_result["table_catalog_updates"],
                        height=300,
                    )

                save_col, discard_col = st.columns(2)
                with save_col:
                    if st.button("Save Imported Definitions"):
                        try:
                            metric_path = (
                                custom_context / "metric_definitions.md"
                            )
                            catalog_path = custom_context / "table_catalog.md"
                            existing_catalog = catalog_path.read_text(
                                encoding="utf-8"
                            )
                            schema_df = st.session_state.get("schema_df")
                            if schema_df is None:
                                schema_df = st.session_state.get("raw_schema_df")
                            table_names = (
                                schema_df["table_name"].unique().tolist()
                                if schema_df is not None
                                else []
                            )
                            merged_catalog = _merge_table_catalog_updates(
                                existing_catalog,
                                catalog_updates_review,
                                table_names,
                            )

                            existing_metrics = (
                                metric_path.read_text(encoding="utf-8").rstrip()
                                if metric_path.is_file()
                                else ""
                            )
                            combined_metrics = "\n\n".join(
                                part
                                for part in (
                                    existing_metrics,
                                    metrics_review.strip(),
                                )
                                if part
                            )
                            _save_catalog(
                                metric_path,
                                combined_metrics.strip() + "\n",
                            )
                            _save_catalog(catalog_path, merged_catalog)
                            st.session_state.pop(
                                "structured_import_result",
                                None,
                            )
                            st.success("✅ Structured definitions saved")
                            st.rerun()
                        except Exception as error:
                            st.error(
                                f"Failed to save structured import: {error}"
                            )
                with discard_col:
                    if st.button("Discard Import"):
                        st.session_state.pop("structured_import_result", None)
                        st.rerun()
        with source_tab2:
            st.info("SQL import — coming in next step.")
        with source_tab3:
            st.info("Manual input — coming in next step.")

        if metric_path.is_file():
            st.divider()
            st.subheader("Current Metric Definitions")
            st.markdown(metric_path.read_text(encoding="utf-8"))

    with validate_tab:
        st.info(
            "Complete Data Source and Metric Definitions tabs before validating."
        )
