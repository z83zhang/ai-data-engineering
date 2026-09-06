import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "test-only-placeholder")

from agent import load_context
from metric_import import save_metric_definitions_yaml


class ContextLoadingTests(unittest.TestCase):
    def test_bundled_demo_context_assembly_is_unchanged(self):
        context_dir = Path(__file__).resolve().parents[1] / "context"
        expected = "\n\n".join(
            f"=== {title} ===\n{(context_dir / filename).read_text(encoding='utf-8')}"
            for title, filename in (
                ("TABLE CATALOG", "table_catalog.md"),
                ("METRIC DEFINITIONS", "metric_definitions.md"),
                ("SCHEMA", "schema.sql"),
            )
        )

        self.assertEqual(load_context(), expected)

    def test_custom_yaml_context_includes_all_layers_notes_and_relationships(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory)
            (path / "table_catalog.md").write_text("Custom catalog", encoding="utf-8")
            (path / "schema.sql").write_text("CREATE TABLE orders;", encoding="utf-8")
            (path / "metric_definitions.md").write_text(
                "LEGACY CONTENT MUST NOT BE USED", encoding="utf-8"
            )
            save_metric_definitions_yaml(path / "metric_definitions.yaml", document())

            context = load_context(context_dir=path)

        self.assertIn("=== METRIC DEFINITIONS ===\nentities:", context)
        self.assertIn("dimensions:", context)
        self.assertIn("measures:", context)
        self.assertIn("metrics:", context)
        self.assertIn("notes:", context)
        self.assertIn("description: Analyst-approved revenue", context)
        self.assertIn("provenance: analyst_edited", context)
        self.assertIn("=== DERIVED RELATIONSHIPS ===", context)
        self.assertIn("- orders.customer_id -> customers (foreign key)", context)
        self.assertNotIn("LEGACY CONTENT MUST NOT BE USED", context)

    def test_legacy_custom_context_remains_a_fallback_when_yaml_is_absent(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory)
            (path / "table_catalog.md").write_text("Custom catalog", encoding="utf-8")
            (path / "schema.sql").write_text("CREATE TABLE orders;", encoding="utf-8")
            (path / "metric_definitions.md").write_text(
                "Legacy custom metrics", encoding="utf-8"
            )

            context = load_context(context_dir=path)

        self.assertIn("=== METRIC DEFINITIONS ===\nLegacy custom metrics", context)
        self.assertNotIn("=== DERIVED RELATIONSHIPS ===", context)


def document():
    return {
        "entities": {
            "orders": {
                "source": {"type": "table", "value": "orders"},
                "keys": [
                    {"column": "order_id", "type": "primary"},
                    {
                        "column": "customer_id",
                        "type": "foreign",
                        "references_entity": "customers",
                    },
                ],
            },
            "customers": {
                "source": {"type": "table", "value": "customers"},
                "keys": [{"column": "customer_id", "type": "primary"}],
            },
        },
        "dimensions": [
            {
                "name": "orders.status",
                "entity": "orders",
                "column": "status",
                "type": "categorical",
            }
        ],
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
            "orders.revenue": {
                "type": "simple",
                "measure": "orders.revenue",
                "filters": [],
            }
        },
        "notes": {
            "orders.revenue": {
                "description": "Analyst-approved revenue",
                "business_rules": "Exclude test orders",
                "caveats": "",
                "ambiguity_rules": "",
                "provenance": "analyst_edited",
            }
        },
    }


if __name__ == "__main__":
    unittest.main()
