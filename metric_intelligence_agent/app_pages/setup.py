import json
import re
import shutil
from datetime import datetime
from pathlib import Path

import streamlit as st

from agent import client
from connectors.duckdb import DuckDBConnector
from connectors.sqlite import SQLiteConnector
from metric_import import (
    parse_dbt_manifest_metrics,
    parse_sql_metric_facts,
    merge_metric_definitions,
    render_metric_markdown,
)


CONNECTION_TYPES = [
    "DuckDB file (.db, .duckdb)",
    "SQLite file (.sqlite, .db)",
    "PostgreSQL — coming soon",
    "Snowflake — coming soon",
    "BigQuery — coming soon",
]

COMING_SOON_TYPES = CONNECTION_TYPES[2:]

METRIC_MARKDOWN_SKELETON = """# Metric Definitions

## [Metric name from extracted source]

- Definition: [Extracted description or empty]
- Formula: [Extracted formula or empty]
- Source tables: [Extracted table names or empty]
- Column references: [Extracted table and column pairs or empty]
- Join conditions: [Extracted join conditions or empty]
- Filters: [Extracted filters or empty]
- Grain: [Extracted grain or empty]
- Trust level: [Extracted trust information or empty]
"""


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


def _schema_grounding_issues(metrics, schema_df):
    """Return extracted table and column references absent from the schema."""
    schema_columns = {}
    if schema_df is not None:
        for _, row in schema_df.iterrows():
            table_name = str(row["table_name"])
            identifiers = [table_name]
            if "table_schema" in schema_df.columns:
                identifiers.append(f"{row['table_schema']}.{table_name}")
            for identifier in identifiers:
                schema_columns.setdefault(identifier.casefold(), set()).add(
                    str(row["column_name"]).casefold()
                )

    unknown_tables = {}
    unknown_columns = {}
    for metric in metrics:
        for table_name in metric["source_tables"]:
            key = table_name.casefold()
            if key not in schema_columns:
                unknown_tables.setdefault(key, table_name)
        for reference in metric["column_references"]:
            table_name = reference["table_name"]
            column_name = reference["column_name"]
            table_key = table_name.casefold()
            if table_key not in schema_columns:
                unknown_tables.setdefault(table_key, table_name)
                continue
            if column_name.casefold() not in schema_columns[table_key]:
                label = f"{table_name}.{column_name}"
                unknown_columns.setdefault(label.casefold(), label)

    return (
        sorted(unknown_tables.values(), key=str.casefold),
        sorted(unknown_columns.values(), key=str.casefold),
    )


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


def _save_metric_definitions(metric_path, content):
    existing = metric_path.read_text(encoding="utf-8") if metric_path.is_file() else ""
    _save_catalog(metric_path, merge_metric_definitions(existing, content))


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
                            metrics = parse_dbt_manifest_metrics(json.loads(file_content))
                            if not metrics:
                                st.warning(
                                    "No metric definitions found in this supported "
                                    "dbt manifest shape."
                                )
                                st.stop()
                            schema_df = st.session_state.get("schema_df")
                            if schema_df is None:
                                schema_df = st.session_state.get("raw_schema_df")
                            unknown_tables, unknown_columns = _schema_grounding_issues(
                                metrics, schema_df
                            )
                            st.session_state.structured_import_result = {
                                "metric_definitions": render_metric_markdown(metrics),
                                "layer_suggestions": [],
                                "import_warnings": [
                                    metric["name"]
                                    for metric in metrics
                                    if metric.get("sql_parse_failed")
                                ],
                                "unknown_tables": unknown_tables,
                                "unknown_columns": unknown_columns,
                            }
                            st.rerun()
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
                                            "not found. Extract every explicit "
                                            "table and column reference as separate "
                                            "table_name/column_name pairs. A "
                                            "source-provided materialization or layer "
                                            "may be returned only as suggested_layer "
                                            "for analyst information; it is never "
                                            "authoritative."
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
                                                            "column_references": {
                                                                "type": "array",
                                                                "items": {
                                                                    "type": "object",
                                                                    "properties": {
                                                                        "table_name": {
                                                                            "type": "string"
                                                                        },
                                                                        "column_name": {
                                                                            "type": "string"
                                                                        },
                                                                    },
                                                                    "required": [
                                                                        "table_name",
                                                                        "column_name",
                                                                    ],
                                                                    "additionalProperties": False,
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
                                                            "column_references",
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
                                                            "suggested_layer": {
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
                                                            "suggested_layer",
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
                                "Choose a file that explicitly defines metrics."
                            )
                            st.stop()

                        schema_df = st.session_state.get("schema_df")
                        if schema_df is None:
                            schema_df = st.session_state.get("raw_schema_df")
                        unknown_tables, unknown_columns = (
                            _schema_grounding_issues(metrics, schema_df)
                        )
                        if unknown_tables:
                            st.warning(
                                "Potential hallucination: source tables not "
                                f"found in the connected schema: {unknown_tables}"
                            )
                        if unknown_columns:
                            st.warning(
                                "Potential hallucination: source columns not "
                                f"found in the connected schema: {unknown_columns}"
                            )

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
                                            f"{METRIC_MARKDOWN_SKELETON}"
                                        ),
                                    },
                                ],
                                temperature=0,
                            )
                        metric_definitions = _clean_catalog_response(
                            markdown_response.choices[0].message.content.strip()
                        )

                        layer_suggestions = []
                        for update in extraction["table_updates"]:
                            if update["suggested_layer"]:
                                layer_suggestions.append(
                                    {
                                        "table_name": update["table_name"],
                                        "suggested_layer": update[
                                            "suggested_layer"
                                        ],
                                    }
                                )
                        st.session_state.structured_import_result = {
                            "metric_definitions": metric_definitions,
                            "layer_suggestions": layer_suggestions,
                            "unknown_tables": unknown_tables,
                            "unknown_columns": unknown_columns,
                        }
                    except Exception as error:
                        st.error(f"Failed to import structured file: {error}")

            if st.session_state.get("structured_import_result"):
                import_result = st.session_state.structured_import_result
                unknown_tables = import_result.get("unknown_tables", [])
                unknown_columns = import_result.get("unknown_columns", [])
                has_grounding_issues = bool(unknown_tables or unknown_columns)
                for metric_name in import_result.get("import_warnings", []):
                    st.warning(
                        f"SQL could not be parsed for {metric_name or 'this metric'} — "
                        "formula, filters, and joins were left blank; complete them "
                        "manually or use the SQL-paste tab instead."
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

                st.subheader("Extracted Metric Definitions")
                st.markdown(import_result["metric_definitions"])
                with st.expander("Edit manually"):
                    metrics_review = st.text_area(
                        "Edit metric definitions",
                        value=import_result["metric_definitions"],
                        height=400,
                    )

                if import_result.get("layer_suggestions"):
                    suggestions = ", ".join(
                        f"{item['table_name']}: {item['suggested_layer']}"
                        for item in import_result["layer_suggestions"]
                    )
                    st.info(
                        "Source-provided layer suggestions (informational "
                        f"only; not saved): {suggestions}"
                    )

                grounding_acknowledged = not has_grounding_issues
                if has_grounding_issues:
                    grounding_acknowledged = st.checkbox(
                        "I acknowledge these references are not present in "
                        "the discovered schema and want to save anyway.",
                        key="structured_import_grounding_ack",
                    )

                save_col, discard_col = st.columns(2)
                with save_col:
                    if st.button(
                        "Save Imported Definitions",
                        disabled=not grounding_acknowledged,
                    ):
                        try:
                            metric_path = (
                                custom_context / "metric_definitions.md"
                            )
                            _save_metric_definitions(metric_path, metrics_review)
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
                        st.session_state.pop(
                            "structured_import_grounding_ack",
                            None,
                        )
                        st.rerun()
        with source_tab2:
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
                    metric_facts = parse_sql_metric_facts(pasted_sql)
                    if not metric_facts:
                        raise ValueError(
                            "No aggregate metric expressions were found in the SELECT list."
                        )
                    metrics = []
                    multiple_metrics = len(metric_facts) > 1
                    with st.spinner("Writing metric descriptions..."):
                        for facts in metric_facts:
                            prose_hint = facts["name"] or (
                                "" if multiple_metrics else metric_name_hint
                            )
                            prose = _metric_prose(facts, prose_hint, sql_trust)
                            metrics.append(
                                {
                                    "name": (
                                        facts["name"]
                                        if multiple_metrics
                                        else metric_name_hint.strip()
                                        or facts["name"]
                                        or prose["name"]
                                    ),
                                    "description": prose["description"],
                                    "formula": facts["formula"],
                                    "source_tables": facts["source_tables"],
                                    "column_references": facts["column_references"],
                                    "join_conditions": facts["join_conditions"],
                                    "filters": facts["filters"],
                                    "grain": ", ".join(facts["group_by"])
                                    or prose["grain"],
                                    "business_rules": prose["business_rules"],
                                    "trust_level": sql_trust,
                                }
                            )
                    schema_df = st.session_state.get("schema_df")
                    if schema_df is None:
                        schema_df = st.session_state.get("raw_schema_df")
                    unknown_tables, unknown_columns = _schema_grounding_issues(
                        metrics, schema_df
                    )
                    st.session_state.sql_import_result = {
                        "metric_definitions": render_metric_markdown(metrics),
                        "metric_count": len(metrics),
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
                st.subheader("Extracted Metric Definition")
                sql_review = st.text_area(
                    "Review metric definition",
                    value=sql_result["metric_definitions"],
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
                save_col, discard_col = st.columns(2)
                with save_col:
                    if st.button("Save SQL Metric", disabled=not acknowledged):
                        _save_metric_definitions(metric_path, sql_review)
                        st.session_state.pop("sql_import_result", None)
                        st.session_state.sql_import_saved = True
                        st.rerun()
                with discard_col:
                    if st.button("Discard SQL Import"):
                        st.session_state.pop("sql_import_result", None)
                        st.session_state.pop("sql_import_grounding_ack", None)
                        st.rerun()
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
