from abc import ABC, abstractmethod

import pandas as pd
import streamlit as st

from agent import get_openai_client


MAX_SCHEMA_CHARS = 32000


class BaseConnector(ABC):
    """Common interface for read-only database connectors."""

    def __init__(self):
        self.connection = None

    @abstractmethod
    def connect(self, credentials: dict):
        """Create and retain a database connection."""

    @abstractmethod
    def test_connection(self) -> bool:
        """Return whether the retained connection can execute a simple query."""

    @abstractmethod
    def discover_schema(self) -> pd.DataFrame:
        """Return table_name, column_name, and data_type metadata."""

    def generate_schema_sql(self, schema_df: pd.DataFrame) -> str:
        """Generate CREATE TABLE statements for discovered schema metadata."""
        return self._generate_schema_sql(schema_df, get_openai_client())

    def _generate_schema_sql(self, schema_df: pd.DataFrame, openai_client) -> str:
        required_columns = {"table_name", "column_name", "data_type"}
        missing_columns = required_columns.difference(schema_df.columns)
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(f"Schema metadata is missing required columns: {missing}")

        schema_text = schema_df.to_string(index=False)
        if len(schema_text) > MAX_SCHEMA_CHARS:
            table_summary = {}
            for _, row in schema_df.iterrows():
                table_name = row["table_name"]
                if table_name not in table_summary:
                    table_summary[table_name] = []
                table_summary[table_name].append(
                    f"{row['column_name']} ({row['data_type']})"
                )
            schema_text = "\n".join(
                f"{table_name}: {', '.join(columns)}"
                for table_name, columns in table_summary.items()
            )
            st.warning("Large schema — sending table summaries to LLM.")
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You convert database schema metadata into SQL. "
                            "Return only CREATE TABLE statements that preserve "
                            "the supplied table names, column names, data types, "
                            "and schemas. Do not add constraints or invent "
                            "columns. Return raw SQL without markdown."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Schema metadata:\n{schema_text}",
                    },
                ],
                temperature=0,
            )
        except Exception as error:
            raise RuntimeError(
                f"Failed to generate schema SQL with OpenAI: {error}"
            ) from error

        schema_sql = response.choices[0].message.content.strip()
        if not schema_sql:
            raise RuntimeError("OpenAI returned empty schema SQL.")
        return schema_sql.replace("```sql", "").replace("```", "").strip()

    def _require_connection(self):
        if self.connection is None:
            raise RuntimeError("No database connection is active. Call connect first.")
        return self.connection
