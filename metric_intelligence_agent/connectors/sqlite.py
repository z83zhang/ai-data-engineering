import json
from pathlib import Path

import duckdb
import pandas as pd

from agent import get_openai_client
from connectors.base import BaseConnector


class SQLiteConnector(BaseConnector):
    """Read-only SQLite connector backed by DuckDB's SQLite extension."""

    def __init__(self):
        super().__init__()
        self._schema_sql = None

    def connect(self, credentials: dict):
        path = credentials.get("path")
        if not path:
            raise ValueError("SQLite connection requires a file path.")
        database_path = Path(path)
        if not database_path.is_file():
            raise FileNotFoundError(f"SQLite file not found: {path}")

        escaped_path = str(database_path.resolve()).replace("'", "''")
        try:
            self.connection = duckdb.connect()
            self.connection.execute("INSTALL sqlite")
            self.connection.execute("LOAD sqlite")
            self.connection.execute(
                f"ATTACH '{escaped_path}' AS db (TYPE sqlite, READ_ONLY)"
            )
            return self.connection
        except Exception as error:
            if self.connection is not None:
                self.connection.close()
                self.connection = None
            raise ConnectionError(
                f"Failed to connect to SQLite file '{path}': {error}"
            ) from error

    def test_connection(self) -> bool:
        try:
            self._require_connection().execute("SELECT 1").fetchone()
            return True
        except Exception as error:
            raise ConnectionError(
                f"SQLite connection test failed: {error}"
            ) from error

    def discover_schema(self) -> pd.DataFrame:
        connection = self._require_connection()
        try:
            tables_df = connection.execute("""
                SELECT name
                FROM db.sqlite_master
                WHERE type = 'table'
                AND name NOT LIKE 'sqlite_%'
                ORDER BY name
            """).df()
            tables = tables_df["name"].tolist()
            if not tables:
                raise RuntimeError("No tables were found in the SQLite database.")

            discovered = []
            for table_name in tables:
                quoted_table = table_name.replace('"', '""')
                cursor = connection.execute(
                    f'SELECT * FROM db."{quoted_table}" LIMIT 0'
                )
                for column in cursor.description:
                    discovered.append(
                        {
                            "table_name": table_name,
                            "column_name": column[0],
                            "data_type": str(column[1]),
                        }
                    )
        except Exception as error:
            if isinstance(error, RuntimeError):
                raise
            raise RuntimeError(
                f"Failed to discover SQLite schema: {error}"
            ) from error

        prompt_schema = json.dumps(discovered, default=str)
        try:
            response = get_openai_client().chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Normalize SQLite schema metadata and generate its "
                            "CREATE TABLE statements. Return valid JSON only in "
                            'this shape: {"schema": [{"table_name": string, '
                            '"column_name": string, "data_type": string}], '
                            '"sql": string}. Preserve every supplied identifier '
                            "and data type exactly. Do not invent columns or add "
                            "constraints."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Discovered schema:\n{prompt_schema}",
                    },
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            result = json.loads(response.choices[0].message.content)
            schema_df = pd.DataFrame(result["schema"])
            self._schema_sql = (
                result["sql"]
                .replace("```sql", "")
                .replace("```", "")
                .strip()
            )
        except Exception as error:
            raise RuntimeError(
                f"Failed to normalize SQLite schema with OpenAI: {error}"
            ) from error

        required_columns = ["table_name", "column_name", "data_type"]
        if schema_df.empty or not set(required_columns).issubset(schema_df.columns):
            raise RuntimeError("OpenAI returned invalid SQLite schema metadata.")
        if not self._schema_sql:
            raise RuntimeError("OpenAI returned empty SQLite schema SQL.")
        return schema_df[required_columns]

    def generate_schema_sql(self, schema_df: pd.DataFrame) -> str:
        if self._schema_sql is None:
            raise RuntimeError(
                "SQLite schema SQL is unavailable. Call discover_schema first."
            )
        return self._schema_sql
