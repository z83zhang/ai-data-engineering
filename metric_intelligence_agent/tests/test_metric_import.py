import unittest

from metric_import import (
    merge_metric_definitions,
    parse_dbt_manifest_metrics,
    parse_sql_facts,
    parse_sql_metric_facts,
    render_metric_markdown,
)


class MetricImportTests(unittest.TestCase):
    def test_metric_merge_replaces_all_case_insensitive_matches_once(self):
        existing = """# Metric Definitions

## Revenue
- Formula: old_one

## order_count
- Formula: COUNT(*)

## REVENUE
- Formula: old_two
"""
        incoming = """# Metric Definitions

## revenue
- Formula: SUM(amount)

## Revenue
- Formula: SUM(net_amount)
"""

        merged = merge_metric_definitions(existing, incoming)

        headings = [
            line[3:]
            for line in merged.splitlines()
            if line.startswith("## ")
        ]
        self.assertEqual(
            [heading.casefold() for heading in headings].count("revenue"), 1
        )
        self.assertIn("## order_count", merged)
        self.assertIn("- Formula: SUM(net_amount)", merged)
        self.assertNotIn("old_one", merged)
        self.assertNotIn("old_two", merged)

    def test_sql_facts_include_explicit_query_structure(self):
        facts = parse_sql_facts(
            """
            SELECT c.customer_id, SUM(o.amount) AS revenue
            FROM customers c
            JOIN orders o ON c.customer_id = o.customer_id
            WHERE o.status = 'paid'
            GROUP BY c.customer_id
            HAVING SUM(o.amount) > 0
            """
        )

        self.assertEqual(facts["source_tables"], ["customers", "orders"])
        self.assertIn(
            {"table_name": "orders", "column_name": "amount"},
            facts["column_references"],
        )
        self.assertEqual(facts["formulas"], ["SUM(o.amount)"])
        self.assertEqual(facts["join_conditions"], ["c.customer_id = o.customer_id"])
        self.assertEqual(facts["group_by"], ["c.customer_id"])

    def test_multi_aggregate_sql_returns_scoped_metrics_with_shared_facts(self):
        metrics = parse_sql_metric_facts(
            """
            SELECT
                c.region,
                SUM(o.amount) AS revenue,
                COUNT(DISTINCT o.order_id) AS order_count,
                SUM(o.amount) / COUNT(DISTINCT o.order_id) AS avg_order_value
            FROM orders o
            JOIN customers c ON o.customer_id = c.customer_id
            WHERE o.status = 'paid'
            GROUP BY c.region
            """
        )

        self.assertEqual(
            [metric["name"] for metric in metrics],
            ["revenue", "order_count", "avg_order_value"],
        )
        self.assertEqual(metrics[0]["formula"], "SUM(o.amount)")
        self.assertEqual(metrics[1]["formula"], "COUNT(DISTINCT o.order_id)")
        self.assertEqual(
            metrics[2]["formula"],
            "SUM(o.amount) / COUNT(DISTINCT o.order_id)",
        )
        for metric in metrics:
            self.assertEqual(metric["source_tables"], ["orders", "customers"])
            self.assertEqual(
                metric["join_conditions"], ["o.customer_id = c.customer_id"]
            )
            self.assertEqual(metric["filters"], ["o.status = 'paid'"])
            self.assertEqual(metric["group_by"], ["c.region"])
            self.assertIn(
                {"table_name": "orders", "column_name": "status"},
                metric["column_references"],
            )
            self.assertIn(
                {"table_name": "customers", "column_name": "region"},
                metric["column_references"],
            )
        self.assertNotIn(
            {"table_name": "orders", "column_name": "amount"},
            metrics[1]["column_references"],
        )

    def test_exact_three_aggregate_query_renders_three_alias_named_sections(self):
        metrics = parse_sql_metric_facts(
            """
            SELECT
                SUM(orders.amount) AS revenue,
                COUNT(DISTINCT orders.order_id) AS order_count,
                SUM(orders.amount) / COUNT(DISTINCT orders.order_id) AS avg_order_value
            FROM orders
            WHERE orders.status NOT IN ('return_pending', 'returned')
            """
        )

        markdown = render_metric_markdown(metrics)

        self.assertEqual(len(metrics), 3)
        self.assertEqual(markdown.count("\n## "), 3)
        self.assertIn("## revenue\n", markdown)
        self.assertIn("## order_count\n", markdown)
        self.assertIn("## avg_order_value\n", markdown)
        self.assertIn("- Formula: SUM(orders.amount)", markdown)
        self.assertIn("- Formula: COUNT(DISTINCT orders.order_id)", markdown)
        self.assertIn(
            "- Formula: SUM(orders.amount) / COUNT(DISTINCT orders.order_id)",
            markdown,
        )

    def test_supported_dbt_manifest_reads_measure_and_model_sql(self):
        manifest = {
            "metadata": {
                "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v11.json"
            },
            "nodes": {
                "model.project.orders": {
                    "name": "orders",
                    "relation_name": "analytics.orders",
                    "raw_code": "select order_id, amount from raw_orders",
                }
            },
            "semantic_models": {
                "semantic_model.project.orders": {
                    "depends_on": {"nodes": ["model.project.orders"]},
                    "measures": [{"name": "revenue", "agg": "sum", "expr": "amount"}],
                }
            },
            "metrics": {
                "metric.project.revenue": {
                    "name": "revenue",
                    "description": "Revenue from orders",
                    "type": "simple",
                    "type_params": {"measure": "revenue"},
                }
            },
        }

        metrics = parse_dbt_manifest_metrics(manifest)

        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0]["formula"], "SUM(amount)")
        self.assertEqual(metrics[0]["source_tables"], ["raw_orders"])

    def test_unsupported_dbt_manifest_is_not_guessed(self):
        manifest = {
            "metadata": {
                "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"
            },
            "metrics": {"metric.project.revenue": {}},
            "semantic_models": {"semantic_model.project.orders": {}},
        }

        self.assertEqual(parse_dbt_manifest_metrics(manifest), [])

    def test_dbt_manifest_falls_back_from_jinja_raw_code_to_compiled_sql(self):
        manifest = self._dbt_manifest(
            raw_code="select order_id, amount from {{ ref('raw_orders') }}",
            compiled_code="select order_id, amount from analytics.raw_orders",
        )

        metric = parse_dbt_manifest_metrics(manifest)[0]

        self.assertFalse(metric["sql_parse_failed"])
        self.assertEqual(metric["source_tables"], ["analytics.raw_orders"])
        self.assertEqual(metric["formula"], "SUM(amount)")
        self.assertIn(
            {"table_name": "analytics.raw_orders", "column_name": "amount"},
            metric["column_references"],
        )

    def test_dbt_manifest_marks_metric_when_model_sql_cannot_be_parsed(self):
        manifest = self._dbt_manifest(
            raw_code="select * from {{ ref('raw_orders') }}",
            compiled_code="select from ???",
        )

        metric = parse_dbt_manifest_metrics(manifest)[0]

        self.assertTrue(metric["sql_parse_failed"])
        self.assertEqual(metric["source_tables"], ["analytics.orders"])
        self.assertEqual(metric["formula"], "")
        self.assertEqual(metric["filters"], [])
        self.assertEqual(metric["join_conditions"], [])

    @staticmethod
    def _dbt_manifest(raw_code, compiled_code=None):
        node = {
            "name": "orders",
            "relation_name": "analytics.orders",
            "raw_code": raw_code,
        }
        if compiled_code is not None:
            node["compiled_code"] = compiled_code
        return {
            "metadata": {
                "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v11.json"
            },
            "nodes": {"model.project.orders": node},
            "semantic_models": {
                "semantic_model.project.orders": {
                    "depends_on": {"nodes": ["model.project.orders"]},
                    "measures": [
                        {"name": "revenue", "agg": "sum", "expr": "amount"}
                    ],
                }
            },
            "metrics": {
                "metric.project.revenue": {
                    "name": "revenue",
                    "description": "Revenue from orders",
                    "type": "simple",
                    "type_params": {"measure": "revenue"},
                }
            },
        }


if __name__ == "__main__":
    unittest.main()
