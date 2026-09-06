import unittest

import pandas as pd

from grounding import schema_grounding_issues


class GroundingTests(unittest.TestCase):
    def test_quoted_qualified_manifest_table_matches_discovered_table(self):
        schema = pd.DataFrame(
            [
                {
                    "table_schema": "main",
                    "table_name": "orders",
                    "column_name": "order_id",
                    "data_type": "BIGINT",
                },
                {
                    "table_schema": "main",
                    "table_name": "orders",
                    "column_name": "amount",
                    "data_type": "DECIMAL",
                },
            ]
        )
        grounding = [
            {
                "source_tables": ['"jaffle_shop"."main"."orders"'],
                "column_references": [
                    {
                        "table_name": '"jaffle_shop"."main"."orders"',
                        "column_name": "amount",
                    }
                ],
            }
        ]

        self.assertEqual(schema_grounding_issues(grounding, schema), ([], []))

    def test_genuinely_missing_table_still_warns(self):
        schema = pd.DataFrame(
            [
                {
                    "table_name": "orders",
                    "column_name": "order_id",
                    "data_type": "BIGINT",
                }
            ]
        )
        missing = '"jaffle_shop"."main"."payments"'
        grounding = [
            {
                "source_tables": [missing],
                "column_references": [
                    {"table_name": missing, "column_name": "payment_id"}
                ],
            }
        ]

        unknown_tables, unknown_columns = schema_grounding_issues(grounding, schema)

        self.assertEqual(unknown_tables, [missing])
        self.assertEqual(unknown_columns, [])

    def test_genuinely_missing_column_still_warns(self):
        schema = pd.DataFrame(
            [{"table_name": "orders", "column_name": "order_id"}]
        )
        table = '"jaffle_shop"."main"."orders"'
        grounding = [
            {
                "source_tables": [table],
                "column_references": [
                    {"table_name": table, "column_name": "missing_amount"}
                ],
            }
        ]

        unknown_tables, unknown_columns = schema_grounding_issues(grounding, schema)

        self.assertEqual(unknown_tables, [])
        self.assertEqual(unknown_columns, [f"{table}.missing_amount"])


if __name__ == "__main__":
    unittest.main()
