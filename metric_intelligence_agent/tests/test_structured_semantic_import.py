import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd

from grounding import schema_grounding_issues
from metric_import import MetricDefinitionError
from structured_semantic_import import build_structured_semantic_document
from structured_semantic_import import extract_structured_semantic_facts


class StructuredSemanticImportTests(unittest.TestCase):
    def test_handwritten_lookml_fixture_runs_through_grounded_extraction_contract(self):
        source = fixture("orders.model.lkml").read_text(encoding="utf-8")
        client = mock_client(representative_extraction())

        extraction = extract_structured_semantic_facts(
            source, "Other structured format", client
        )
        result = build_structured_semantic_document(extraction)

        request = client.chat.completions.create.call_args.kwargs
        self.assertIn(source, request["messages"][1]["content"])
        self.assertTrue(request["response_format"]["json_schema"]["strict"])
        self.assertEqual(result["unresolved_keys"], [])
        self.assertIn("revenue", result["document"]["metrics"])

    def test_handwritten_cube_fixture_skips_composed_metric(self):
        source = fixture("orders_cube.js").read_text(encoding="utf-8")
        extraction_payload = representative_extraction()
        extraction_payload["metrics"].append(
            metric_record(
                "revenue_per_customer",
                measure="",
                requires_metric_composition=True,
                composition_reason="references revenue and customer count metrics",
            )
        )
        client = mock_client(extraction_payload)

        extraction = extract_structured_semantic_facts(
            source, "Other structured format", client
        )
        result = build_structured_semantic_document(extraction)

        self.assertNotIn("revenue_per_customer", result["document"]["metrics"])
        self.assertTrue(
            any("revenue_per_customer" in warning for warning in result["warnings"])
        )

    def test_declared_relationship_resolves_and_layered_document_validates(self):
        result = build_structured_semantic_document(representative_extraction())

        self.assertEqual(result["unresolved_keys"], [])
        self.assertIn(
            {
                "column": "customer_id",
                "type": "foreign",
                "references_entity": "customers",
            },
            result["document"]["entities"]["orders"]["keys"],
        )
        self.assertEqual(
            result["document"]["metrics"]["revenue"],
            {"type": "simple", "measure": "orders.revenue", "filters": []},
        )

    def test_ambiguous_relationship_requires_confirmation(self):
        extraction = representative_extraction()
        extraction["relationships"][0]["direction"] = "unresolved"
        extraction["relationships"][0]["explicitly_declared"] = False

        unresolved = build_structured_semantic_document(extraction)
        candidate = unresolved["unresolved_keys"][0]
        self.assertFalse(
            any(
                key["type"] == "foreign"
                for entity in unresolved["document"]["entities"].values()
                for key in entity["keys"]
            )
        )

        resolved = build_structured_semantic_document(
            extraction,
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

    def test_direction_is_not_trusted_without_explicit_declaration(self):
        extraction = representative_extraction()
        extraction["relationships"][0]["explicitly_declared"] = False

        result = build_structured_semantic_document(extraction)

        self.assertEqual(len(result["unresolved_keys"]), 1)

    def test_metric_composition_is_skipped_with_warning(self):
        extraction = representative_extraction()
        extraction["metrics"].append(
            metric_record(
                "revenue_per_customer",
                measure="",
                requires_metric_composition=True,
                composition_reason="references revenue and customer_count metrics",
            )
        )

        result = build_structured_semantic_document(extraction)

        self.assertNotIn("revenue_per_customer", result["document"]["metrics"])
        self.assertTrue(
            any("revenue_per_customer" in warning for warning in result["warnings"])
        )

    def test_filter_must_reference_declared_dimension_or_key(self):
        extraction = representative_extraction()
        extraction["metrics"][0]["filters"] = [
            {"dimension": "orders.raw_status", "operator": "=", "value": "paid"}
        ]

        with self.assertRaisesRegex(MetricDefinitionError, "undeclared dimension"):
            build_structured_semantic_document(extraction)

    def test_grounding_contains_entity_dimension_measure_and_relationship_columns(self):
        result = build_structured_semantic_document(representative_extraction())

        self.assertEqual(result["grounding"]["source_tables"], ["main.orders", "main.customers"])
        self.assertIn(
            {"table_name": "main.orders", "column_name": "amount"},
            result["grounding"]["column_references"],
        )
        self.assertIn(
            {"table_name": "main.orders", "column_name": "customer_id"},
            result["grounding"]["column_references"],
        )
        self.assertIn(
            {"table_name": "main.customers", "column_name": "customer_id"},
            result["grounding"]["column_references"],
        )

    def test_grounding_matches_qualified_tables_but_reports_real_missing_column(self):
        result = build_structured_semantic_document(representative_extraction())
        schema = pd.DataFrame(
            [
                {"table_name": "orders", "column_name": column}
                for column in ("order_id", "customer_id", "status")
            ]
            + [{"table_name": "customers", "column_name": "customer_id"}]
        )

        unknown_tables, unknown_columns = schema_grounding_issues(
            [result["grounding"]], schema
        )

        self.assertEqual(unknown_tables, [])
        self.assertEqual(unknown_columns, ["main.orders.amount"])


def representative_extraction():
    return {
        "entities": [
            {
                "name": "orders",
                "source_table": "main.orders",
                "keys": [{"column": "order_id", "type": "primary"}],
            },
            {
                "name": "customers",
                "source_table": "main.customers",
                "keys": [{"column": "customer_id", "type": "primary"}],
            },
        ],
        "dimensions": [
            {
                "name": "orders.status",
                "entity": "orders",
                "column": "status",
                "type": "categorical",
                "granularity": "",
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
        "metrics": [metric_record("revenue", measure="orders.revenue")],
        "relationships": [
            {
                "left_entity": "orders",
                "left_column": "customer_id",
                "right_entity": "customers",
                "right_column": "customer_id",
                "direction": "left_references_right",
                "explicitly_declared": True,
            }
        ],
        "warnings": [],
        "layer_suggestions": [],
    }


def metric_record(
    name,
    *,
    measure,
    requires_metric_composition=False,
    composition_reason="",
):
    return {
        "name": name,
        "type": "simple",
        "measure": measure,
        "numerator": "",
        "denominator": "",
        "expression": "",
        "window": "",
        "filters": [],
        "grain": [],
        "description": "Gross order revenue",
        "business_rules": "",
        "caveats": "",
        "ambiguity_rules": "",
        "requires_metric_composition": requires_metric_composition,
        "composition_reason": composition_reason,
    }


def fixture(name):
    return Path(__file__).parent / "fixtures" / "structured_import" / name


def mock_client(payload):
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload))
            )
        ]
    )
    return client


if __name__ == "__main__":
    unittest.main()
