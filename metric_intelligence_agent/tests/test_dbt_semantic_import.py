import unittest

from dbt_semantic_import import extract_dbt_semantic_document


class DbtSemanticImportTests(unittest.TestCase):
    def test_relationship_test_becomes_directed_foreign_key(self):
        result = extract_dbt_semantic_document(representative_manifest())

        document = result["document"]
        self.assertEqual(result["manifest_version"], 11)
        self.assertEqual(result["unresolved_keys"], [])
        self.assertEqual(
            document["entities"]["orders"]["source"],
            {"type": "table", "value": "analytics.orders"},
        )
        self.assertIn(
            {
                "column": "customer_id",
                "type": "foreign",
                "references_entity": "customers",
            },
            document["entities"]["orders"]["keys"],
        )
        self.assertEqual(document["entities"]["customers"]["keys"], [])
        self.assertEqual(
            document["measures"],
            [
                {
                    "name": "orders.revenue",
                    "entity": "orders",
                    "expression": "SUM(amount)",
                    "aggregation": "sum",
                    "re_aggregatable": True,
                }
            ],
        )
        self.assertEqual(
            document["metrics"]["revenue"],
            {"type": "simple", "measure": "orders.revenue", "filters": []},
        )

    def test_relationship_missing_direction_stays_unresolved(self):
        manifest = representative_manifest()
        relationship = manifest["nodes"]["test.project.orders_customer_relationship"]
        relationship.pop("attached_node")
        relationship["test_metadata"]["kwargs"].pop("to")

        result = extract_dbt_semantic_document(manifest)

        self.assertEqual(len(result["unresolved_keys"]), 1)
        candidate = result["unresolved_keys"][0]
        self.assertEqual(candidate["kind"], "relationship")
        self.assertFalse(
            any(
                key["type"] == "foreign"
                for entity in result["document"]["entities"].values()
                for key in entity["keys"]
            )
        )

        resolved = extract_dbt_semantic_document(
            manifest,
            relationship_resolutions={candidate["id"]: "left_references_right"},
        )
        self.assertEqual(resolved["unresolved_keys"], [])
        self.assertIn(
            {
                "column": "customer_id",
                "type": "foreign",
                "references_entity": "customers",
            },
            resolved["document"]["entities"]["orders"]["keys"],
        )

    def test_manifest_without_metrics_still_extracts_semantic_facts(self):
        manifest = representative_manifest()
        manifest["metrics"] = {}

        result = extract_dbt_semantic_document(manifest)

        self.assertEqual(result["document"]["metrics"], {})
        self.assertEqual(result["document"]["notes"], {})
        self.assertTrue(result["document"]["entities"])
        self.assertTrue(result["document"]["dimensions"])
        self.assertTrue(result["document"]["measures"])

    def test_unsupported_manifest_version_is_not_interpreted(self):
        manifest = representative_manifest()
        manifest["metadata"]["dbt_schema_version"] = (
            "https://schemas.getdbt.com/dbt/manifest/v12.json"
        )

        self.assertIsNone(extract_dbt_semantic_document(manifest))

    def test_v10_manifest_remains_supported(self):
        manifest = representative_manifest()
        manifest["metadata"]["dbt_schema_version"] = (
            "https://schemas.getdbt.com/dbt/manifest/v10.json"
        )

        self.assertEqual(extract_dbt_semantic_document(manifest)["manifest_version"], 10)

    def test_parseable_metric_filter_uses_dimension_and_bare_value(self):
        manifest = representative_manifest()
        manifest["metrics"]["metric.project.revenue"]["filter"] = "status = 'paid'"

        metric = extract_dbt_semantic_document(manifest)["document"]["metrics"][
            "revenue"
        ]

        self.assertEqual(
            metric["filters"],
            [{"dimension": "orders.status", "operator": "=", "value": "paid"}],
        )

    def test_manifest_foreign_key_constraint_is_a_direction_signal(self):
        manifest = representative_manifest()
        manifest["nodes"].pop("test.project.orders_customer_relationship")
        manifest["nodes"]["model.project.orders"]["constraints"] = [
            {
                "type": "foreign_key",
                "columns": ["customer_id"],
                "expression": "references analytics.customers (customer_id)",
            }
        ]

        result = extract_dbt_semantic_document(manifest)

        self.assertEqual(result["unresolved_keys"], [])
        self.assertIn(
            {
                "column": "customer_id",
                "type": "foreign",
                "references_entity": "customers",
            },
            result["document"]["entities"]["orders"]["keys"],
        )

    def test_structured_fk_constraint_is_preferred_direction_signal(self):
        manifest = representative_manifest()
        manifest["nodes"].pop("test.project.orders_customer_relationship")
        manifest["nodes"]["model.project.orders"]["constraints"] = [
            {
                "type": "foreign_key",
                "columns": ["customer_id"],
                "to": "ref('customers')",
                "to_columns": ["customer_id"],
            }
        ]

        result = extract_dbt_semantic_document(manifest)

        self.assertEqual(result["unresolved_keys"], [])
        self.assertIn(
            {
                "column": "customer_id",
                "type": "foreign",
                "references_entity": "customers",
            },
            result["document"]["entities"]["orders"]["keys"],
        )

    def test_structured_fk_to_missing_entity_warns_without_guessing(self):
        manifest = representative_manifest()
        manifest["nodes"].pop("test.project.orders_customer_relationship")
        manifest["nodes"]["model.project.orders"]["constraints"] = [
            {
                "type": "foreign_key",
                "columns": ["customer_id"],
                "to": "ref('missing_customers')",
                "to_columns": ["customer_id"],
            }
        ]

        result = extract_dbt_semantic_document(manifest)

        self.assertTrue(
            any(
                "referenced entity is unresolved" in warning
                for warning in result["warnings"]
            )
        )
        self.assertFalse(
            any(
                key["type"] == "foreign"
                for entity in result["document"]["entities"].values()
                for key in entity["keys"]
            )
        )


def representative_manifest():
    return {
        "metadata": {
            "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v11.json"
        },
        "nodes": {
            "model.project.orders": {
                "resource_type": "model",
                "name": "orders",
                "relation_name": "analytics.orders",
            },
            "model.project.customers": {
                "resource_type": "model",
                "name": "customers",
                "relation_name": "analytics.customers",
            },
            "test.project.orders_customer_relationship": {
                "resource_type": "test",
                "name": "relationships_orders_customer_id",
                "attached_node": "model.project.orders",
                "column_name": "customer_id",
                "test_metadata": {
                    "name": "relationships",
                    "kwargs": {
                        "column_name": "customer_id",
                        "field": "customer_id",
                        "to": "ref('customers')",
                    },
                },
                "depends_on": {
                    "nodes": ["model.project.orders", "model.project.customers"]
                },
            },
        },
        "semantic_models": {
            "semantic_model.project.orders": {
                "name": "orders",
                "depends_on": {"nodes": ["model.project.orders"]},
                "entities": [
                    {"name": "order", "type": "primary", "expr": "order_id"}
                ],
                "dimensions": [
                    {"name": "status", "type": "categorical", "expr": "status"}
                ],
                "measures": [
                    {"name": "revenue", "agg": "sum", "expr": "amount"}
                ],
            }
        },
        "metrics": {
            "metric.project.revenue": {
                "name": "revenue",
                "description": "Gross order revenue",
                "type": "simple",
                "type_params": {"measure": "revenue"},
            }
        },
    }


if __name__ == "__main__":
    unittest.main()
