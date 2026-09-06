def schema_grounding_issues(metrics, schema_df):
    """Return extracted table and column references absent from the schema."""
    schema_columns = {}
    if schema_df is not None:
        for _, row in schema_df.iterrows():
            table_key = _table_identity(row["table_name"])
            schema_columns.setdefault(table_key, set()).add(
                _identifier_part(row["column_name"]).casefold()
            )

    unknown_tables = {}
    unknown_columns = {}
    for metric in metrics:
        for table_name in metric["source_tables"]:
            key = _table_identity(table_name)
            if key not in schema_columns:
                unknown_tables.setdefault(key, table_name)
        for reference in metric["column_references"]:
            table_name = reference["table_name"]
            column_name = reference["column_name"]
            table_key = _table_identity(table_name)
            if table_key not in schema_columns:
                unknown_tables.setdefault(table_key, table_name)
                continue
            if _identifier_part(column_name).casefold() not in schema_columns[table_key]:
                label = f"{table_name}.{column_name}"
                unknown_columns.setdefault(label.casefold(), label)

    return (
        sorted(unknown_tables.values(), key=str.casefold),
        sorted(unknown_columns.values(), key=str.casefold),
    )


def _table_identity(value):
    normalized = str(value).strip().replace('"', "").replace("`", "")
    return _identifier_part(normalized.rsplit(".", 1)[-1]).casefold()


def _identifier_part(value):
    return str(value).strip().strip('"`[]')
