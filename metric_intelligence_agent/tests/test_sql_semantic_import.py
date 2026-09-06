import tempfile
import unittest
from pathlib import Path

from metric_import import (
    empty_metric_document,
    load_metric_definitions_yaml,
    merge_metric_documents,
    parse_metric_definitions_yaml,
    render_metric_definitions_yaml,
    save_metric_definitions_yaml,
    update_metric_notes,
)
from sql_semantic_import import (
    apply_confirmed_entity_keys,
    extract_sql_semantic_document,
)


WORKED_EXAMPLE_SQL = """
WITH paid_orders AS (
    SELECT o.customer_id, o.order_id, o.amount
    FROM orders o
    JOIN payments p ON p.order_id = o.order_id AND p.status = 'settled'
    WHERE o.status = 'complete'
),
customer_totals AS (
    SELECT customer_id, SUM(amount) AS total_amount
    FROM paid_orders
    GROUP BY customer_id
)
SELECT AVG(ct.total_amount) AS avg_customer_revenue
FROM customer_totals ct
JOIN customers c ON c.id = ct.customer_id
WHERE c.region = 'US'
"""


class SqlSemanticImportTests(unittest.TestCase):
    def test_worked_example_is_unresolved_before_analyst_confirmation(self):
        result = extract_sql_semantic_document(
            WORKED_EXAMPLE_SQL, "Average Customer Revenue"
        )

        document = result["document"]
        self.assertEqual(
            list(document["entities"]),
            ["paid_orders", "customer_totals", "customers"],
        )
        self.assertEqual(document["entities"]["paid_orders"]["keys"], [])
        self.assertEqual(
            document["entities"]["customer_totals"]["keys"],
            [{"column": "customer_id", "type": "primary"}],
        )
        self.assertEqual(
            {candidate["kind"] for candidate in result["unresolved_keys"]},
            {"key_type", "relationship"},
        )
        self.assertIn(
            "key:paid_orders.order_id",
            {candidate["id"] for candidate in result["unresolved_keys"]},
        )

    def test_worked_example_matches_adr_facts_after_confirmation(self):
        result = extract_sql_semantic_document(
            WORKED_EXAMPLE_SQL,
            "Average Customer Revenue",
            key_resolutions={
                "key:paid_orders.order_id": "primary",
                "relationship:customers.id=paid_orders.customer_id": "right_references_left",
            },
        )

        document = result["document"]
        self.assertEqual(result["unresolved_keys"], [])
        self.assertEqual(set(document["entities"]), {"paid_orders", "customer_totals", "customers"})
        self.assertNotIn("orders", document["entities"])
        self.assertNotIn("payments", document["entities"])
        self.assertEqual(
            document["entities"]["paid_orders"]["keys"],
            [
                {"column": "order_id", "type": "primary"},
                {
                    "column": "customer_id",
                    "type": "foreign",
                    "references_entity": "customers",
                },
            ],
        )
        self.assertEqual(
            document["entities"]["customer_totals"]["keys"],
            [{"column": "customer_id", "type": "primary"}],
        )
        self.assertEqual(
            document["entities"]["customers"]["keys"],
            [{"column": "id", "type": "primary"}],
        )
        self.assertEqual(
            document["measures"],
            [
                {
                    "name": "customer_totals.total_amount_sum",
                    "entity": "customer_totals",
                    "expression": "SUM(amount)",
                    "aggregation": "sum",
                    "re_aggregatable": True,
                }
            ],
        )
        self.assertEqual(
            document["metrics"]["Average Customer Revenue"]["expression"],
            "AVG(customer_totals.total_amount_sum)",
        )
        self.assertEqual(
            document["metrics"]["Average Customer Revenue"]["filters"],
            [{"dimension": "customers.region", "operator": "=", "value": "US"}],
        )

        reviewed = parse_metric_definitions_yaml(
            render_metric_definitions_yaml(document)
        )
        merged = merge_metric_documents(empty_metric_document(), reviewed)
        merged = update_metric_notes(
            merged,
            "Average Customer Revenue",
            description="Average total paid-order revenue per customer, US region only.",
            business_rules="Only settled payments and completed orders count toward revenue.",
            caveats="",
            ambiguity_rules="",
        )
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "metric_definitions.yaml"
            save_metric_definitions_yaml(path, merged)
            reloaded = load_metric_definitions_yaml(path)
        self.assertEqual(reloaded, merged)

    def test_cte_alias_is_not_a_real_grounding_table(self):
        result = extract_sql_semantic_document(
            "WITH totals AS (SELECT customer_id, SUM(amount) total FROM orders GROUP BY customer_id) "
            "SELECT AVG(t.total) AS average_total FROM totals t",
            "Average Total",
        )

        self.assertNotIn("totals", result["grounding"]["source_tables"])
        self.assertEqual(result["grounding"]["source_tables"], ["orders"])

    def test_top_level_filter_is_not_replaced_by_cte_filter(self):
        result = extract_sql_semantic_document(
            "WITH paid AS (SELECT customer_id, amount FROM orders WHERE status = 'paid') "
            "SELECT SUM(p.amount) AS revenue FROM paid p JOIN customers c "
            "ON c.id = p.customer_id WHERE c.region = 'US'",
            "Revenue",
        )

        filters = result["document"]["metrics"]["Revenue"]["filters"]
        self.assertEqual(
            filters,
            [{"dimension": "customers.region", "operator": "=", "value": "US"}],
        )
        self.assertNotIn("status", str(filters).casefold())

    def test_join_without_declared_fk_stays_unresolved(self):
        result = extract_sql_semantic_document(
            "SELECT SUM(o.amount) revenue FROM orders o "
            "JOIN customers c ON o.customer_id = c.id",
            "Revenue",
        )

        relationships = [
            item for item in result["unresolved_keys"] if item["kind"] == "relationship"
        ]
        self.assertEqual(len(relationships), 1)
        for entity in result["document"]["entities"].values():
            self.assertFalse(any(key["type"] == "foreign" for key in entity["keys"]))

    def test_declared_fk_resolves_relationship_without_analyst_direction(self):
        result = extract_sql_semantic_document(
            WORKED_EXAMPLE_SQL,
            "Average Customer Revenue",
            foreign_keys=[
                {
                    "from_entity": "paid_orders",
                    "column": "customer_id",
                    "references_entity": "customers",
                    "references_column": "id",
                }
            ],
        )

        self.assertFalse(
            any(item["kind"] == "relationship" for item in result["unresolved_keys"])
        )
        self.assertIn(
            {
                "column": "customer_id",
                "type": "foreign",
                "references_entity": "customers",
            },
            result["document"]["entities"]["paid_orders"]["keys"],
        )
        self.assertIn(
            {"column": "id", "type": "primary"},
            result["document"]["entities"]["customers"]["keys"],
        )

    def test_confirmed_keys_override_stale_review_then_save(self):
        pre_confirmation = extract_sql_semantic_document(
            WORKED_EXAMPLE_SQL, "Average Customer Revenue"
        )
        stale_review = parse_metric_definitions_yaml(
            render_metric_definitions_yaml(pre_confirmation["document"])
        )
        confirmed = extract_sql_semantic_document(
            WORKED_EXAMPLE_SQL,
            "Average Customer Revenue",
            key_resolutions={
                "key:paid_orders.order_id": "primary",
                "relationship:customers.id=paid_orders.customer_id": "right_references_left",
            },
        )

        reviewed_with_confirmed_keys = apply_confirmed_entity_keys(
            stale_review, confirmed["document"]
        )
        merged = merge_metric_documents(
            empty_metric_document(), reviewed_with_confirmed_keys
        )
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "metric_definitions.yaml"
            save_metric_definitions_yaml(path, merged)
            saved = load_metric_definitions_yaml(path)

        self.assertEqual(
            saved["entities"]["paid_orders"]["keys"],
            [
                {"column": "order_id", "type": "primary"},
                {
                    "column": "customer_id",
                    "type": "foreign",
                    "references_entity": "customers",
                },
            ],
        )
        self.assertEqual(
            saved["entities"]["customers"]["keys"],
            [{"column": "id", "type": "primary"}],
        )

    def test_count_distinct_measure_is_not_reaggregatable(self):
        result = extract_sql_semantic_document(
            "SELECT COUNT(DISTINCT order_id) AS distinct_orders FROM orders"
        )

        measure = result["document"]["measures"][0]
        self.assertEqual(measure["aggregation"], "count_distinct")
        self.assertFalse(measure["re_aggregatable"])


if __name__ == "__main__":
    unittest.main()
