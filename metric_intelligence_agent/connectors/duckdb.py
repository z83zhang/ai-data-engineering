from pathlib import Path

import duckdb
import pandas as pd

from agent import get_openai_client
from connectors.base import BaseConnector


class DuckDBConnector(BaseConnector):
    """Read-only connector for DuckDB database files."""

    def connect(self, credentials: dict):
        path = credentials.get("path")
        if not path:
            raise ValueError("DuckDB connection requires a file path.")
        if not Path(path).is_file():
            raise FileNotFoundError(f"DuckDB file not found: {path}")

        try:
            self.connection = duckdb.connect(str(path), read_only=True)
            return self.connection
        except Exception as error:
            raise ConnectionError(
                f"Failed to connect to DuckDB file '{path}': {error}"
            ) from error

    def test_connection(self) -> bool:
        try:
            self._require_connection().execute("SELECT 1").fetchone()
            return True
        except Exception as error:
            raise ConnectionError(
                f"DuckDB connection test failed: {error}"
            ) from error

    def discover_schema(self) -> pd.DataFrame:
        # information_schema.columns includes columns from both tables and views.
        query = """
            SELECT
                table_schema,
                table_name,
                column_name,
                data_type
            FROM information_schema.columns
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
            ORDER BY table_schema, table_name, ordinal_position
        """
        try:
            schema_df = self._require_connection().execute(query).df()
        except Exception as error:
            raise RuntimeError(
                f"Failed to discover DuckDB schema: {error}"
            ) from error

        if schema_df.empty:
            raise RuntimeError(
                "No user tables or views were found in the DuckDB database."
            )
        if schema_df["table_schema"].nunique() == 1:
            schema_df = schema_df.drop(columns="table_schema")
        return schema_df

    def generate_schema_sql(self, schema_df: pd.DataFrame) -> str:
        try:
            return self._generate_schema_sql(schema_df, get_openai_client())
        except Exception as error:
            if isinstance(error, (ValueError, RuntimeError)):
                raise
            raise RuntimeError(
                f"Failed to generate DuckDB schema SQL: {error}"
            ) from error
