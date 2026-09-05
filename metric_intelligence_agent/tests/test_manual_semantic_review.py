import unittest
from copy import deepcopy

from manual_semantic_review import (
    RelationshipCorrectionError,
    correct_relationship,
    derived_relationships,
    grouped_entities,
    grouped_metrics,
    metric_facts,
    relationship_label,
)
from metric_import import update_metric_notes, validate_metric_document


class ManualSemanticReviewTests(unittest.TestCase):
    def test_metric_facts_are_read_only_and_exclude_notes(self):
        document = example_document()

        facts = metric_facts(document, "REVENUE")
        facts["measure"] = "changed"

        self.assertNotIn("description", facts)
        self.assertEqual(document["metrics"]["revenue"]["measure"], "orders.revenue")

    def test_note_edit_does_not_change_fact_sections(self):
        document = example_document()
        facts_before = deepcopy({key: document[key] for key in ("entities", "dimensions", "measures", "metrics")})

        updated = update_metric_notes(document, "Revenue", caveats="Exclude tests")

        self.assertEqual(
            {key: updated[key] for key in facts_before},
            facts_before,
        )
        self.assertEqual(updated["notes"]["revenue"]["caveats"], "Exclude tests")

    def test_relationships_are_derived_from_foreign_keys(self):
        self.assertEqual(
            derived_relationships(example_document()),
            [
                {
                    "from_entity": "orders",
                    "column": "customer_id",
                    "references_entity": "customers",
                    "key_type": "foreign",
                }
            ],
        )

    def test_entity_group_co_locates_owned_facts_and_connections(self):
        groups = {item["name"]: item for item in grouped_entities(example_document())}

        orders = groups["orders"]
        self.assertEqual(orders["source"]["value"], "orders")
        self.assertEqual(orders["keys"], [{"column": "id", "type": "primary"}])
        self.assertEqual(
            [item["name"] for item in orders["measures"]], ["orders.revenue"]
        )
        self.assertEqual(
            relationship_label(orders["relationships"][0]),
            "orders.customer_id → customers",
        )

    def test_metric_group_co_locates_measure_expression_and_notes(self):
        metric = grouped_metrics(example_document())[0]

        self.assertEqual(metric["name"], "revenue")
        self.assertEqual(metric["facts"]["measure"], "orders.revenue")
        self.assertEqual(metric["measures"][0]["expression"], "SUM(amount)")
        self.assertEqual(metric["notes"]["description"], "Revenue")

    def test_relationship_direction_can_be_corrected(self):
        document = example_document()
        relationship = derived_relationships(document)[0]

        updated = correct_relationship(
            document,
            relationship,
            from_entity="CUSTOMERS",
            column="id",
            key_type="foreign",
            references_entity="ORDERS",
        )

        self.assertNotIn(
            "customer_id", [key["column"] for key in updated["entities"]["orders"]["keys"]]
        )
        self.assertEqual(
            updated["entities"]["customers"]["keys"],
            [{"column": "id", "type": "foreign", "references_entity": "orders"}],
        )
        validate_metric_document(updated)

    def test_relationship_can_be_reclassified_as_non_foreign_key(self):
        document = example_document()
        relationship = derived_relationships(document)[0]

        updated = correct_relationship(
            document,
            relationship,
            from_entity="orders",
            column="customer_id",
            key_type="unique",
        )

        self.assertEqual(derived_relationships(updated), [])
        self.assertIn(
            {"column": "customer_id", "type": "unique"},
            updated["entities"]["orders"]["keys"],
        )

    def test_stale_relationship_edit_is_rejected(self):
        document = example_document()
        relationship = derived_relationships(document)[0]
        document["entities"]["orders"]["keys"] = []

        with self.assertRaisesRegex(RelationshipCorrectionError, "changed"):
            correct_relationship(
                document,
                relationship,
                from_entity="orders",
                column="customer_id",
                key_type="unique",
            )


def example_document():
    return {
        "entities": {
            "orders": {
                "source": {"type": "table", "value": "orders"},
                "keys": [
                    {"column": "id", "type": "primary"},
                    {
                        "column": "customer_id",
                        "type": "foreign",
                        "references_entity": "customers",
                    },
                ],
            },
            "customers": {
                "source": {"type": "table", "value": "customers"},
                "keys": [{"column": "id", "type": "primary"}],
            },
        },
        "dimensions": [],
        "measures": [
            {
                "name": "orders.revenue",
                "entity": "orders",
                "expression": "SUM(amount)",
                "aggregation": "sum",
                "re_aggregatable": True,
            }
        ],
        "metrics": {
            "revenue": {
                "type": "simple",
                "measure": "orders.revenue",
                "filters": [],
                "grain": [],
            }
        },
        "notes": {
            "revenue": {
                "description": "Revenue",
                "business_rules": "",
                "caveats": "",
                "ambiguity_rules": "",
            }
        },
    }


if __name__ == "__main__":
    unittest.main()
