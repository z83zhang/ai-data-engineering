import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from metric_import import (
    MetricDefinitionError,
    RelationshipConflictError,
    RenameConfirmationRequired,
    load_metric_definitions_yaml,
    merge_metric_documents,
    metric_name_near_matches,
    parse_metric_definitions_yaml,
    preserve_analyst_notes,
    relationship_conflicts,
    save_metric_definitions_yaml,
    update_metric_notes,
    validate_metric_document,
)


class MetricYamlTests(unittest.TestCase):
    def test_missing_entity_prefix_is_a_metric_name_near_match(self):
        existing = partial_document(metrics={"orders.revenue": {}})
        incoming = partial_document(metrics={"revenue": {}})

        self.assertEqual(
            metric_name_near_matches(incoming, existing),
            {"revenue": ["orders.revenue"]},
        )

    def test_exact_case_insensitive_metric_name_is_not_a_near_match(self):
        existing = partial_document(metrics={"Revenue": {}})
        incoming = partial_document(metrics={"revenue": {}})

        self.assertEqual(metric_name_near_matches(incoming, existing), {})

    def test_novel_metric_name_has_no_near_match(self):
        existing = partial_document(metrics={"orders.revenue": {}})
        incoming = partial_document(metrics={"totally_new_metric_xyz": {}})

        self.assertEqual(metric_name_near_matches(incoming, existing), {})

    def test_adr_010_worked_example_writes_and_reads_without_loss(self):
        document = worked_example()
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "metric_definitions.yaml"
            save_metric_definitions_yaml(path, document)
            loaded = load_metric_definitions_yaml(path)

        self.assertEqual(loaded, document)
        self.assertEqual(
            loaded["metrics"]["Average Customer Revenue"]["expression"],
            "AVG(customer_totals.total_amount_sum)",
        )

    def test_metric_rename_requires_confirmation_and_preserves_notes(self):
        existing = worked_example()
        incoming = partial_document(
            metrics={
                "Average US Customer Revenue": {
                    **existing["metrics"]["Average Customer Revenue"],
                    "expression": "AVG(customer_totals.total_amount_sum)",
                }
            }
        )

        with self.assertRaises(RenameConfirmationRequired):
            merge_metric_documents(
                existing,
                incoming,
                metric_renames={"Average Customer Revenue": "Average US Customer Revenue"},
            )

        merged = merge_metric_documents(
            existing,
            incoming,
            metric_renames={"Average Customer Revenue": "Average US Customer Revenue"},
            confirm_renames=True,
        )
        self.assertNotIn("Average Customer Revenue", merged["metrics"])
        self.assertIn("Average US Customer Revenue", merged["metrics"])
        self.assertEqual(
            merged["notes"]["Average US Customer Revenue"]["description"],
            existing["notes"]["Average Customer Revenue"]["description"],
        )

    def test_relationship_conflict_is_surfaced_before_entity_replacement(self):
        existing = worked_example()
        changed = deepcopy(existing["entities"]["paid_orders"])
        changed["keys"][1]["references_entity"] = "customer_totals"
        incoming = partial_document(entities={"paid_orders": changed})

        conflicts = relationship_conflicts(existing, incoming)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["column"], "customer_id")
        with self.assertRaises(RelationshipConflictError):
            merge_metric_documents(existing, incoming)

        merged = merge_metric_documents(
            existing, incoming, confirm_relationship_conflicts=True
        )
        self.assertEqual(
            merged["entities"]["paid_orders"]["keys"][1]["references_entity"],
            "customer_totals",
        )

    def test_dropped_foreign_key_is_a_relationship_conflict(self):
        existing = worked_example()
        changed = deepcopy(existing["entities"]["paid_orders"])
        changed["keys"] = [changed["keys"][0]]

        with self.assertRaises(RelationshipConflictError):
            merge_metric_documents(
                existing, partial_document(entities={"paid_orders": changed})
            )

    def test_changed_relationship_key_type_is_a_conflict(self):
        existing = worked_example()
        changed = deepcopy(existing["entities"]["paid_orders"])
        changed["keys"][1] = {"column": "customer_id", "type": "unique"}

        with self.assertRaises(RelationshipConflictError):
            merge_metric_documents(
                existing, partial_document(entities={"paid_orders": changed})
            )

    def test_derived_metric_cannot_aggregate_non_reaggregatable_measure(self):
        document = worked_example()
        document["measures"][0]["re_aggregatable"] = False

        with self.assertRaisesRegex(MetricDefinitionError, "non-re-aggregatable"):
            validate_metric_document(document)

    def test_derived_metric_must_aggregate_a_declared_measure(self):
        document = worked_example()
        document["metrics"]["Average Customer Revenue"]["expression"] = "AVG(missing.value)"

        with self.assertRaisesRegex(MetricDefinitionError, "declared measure"):
            validate_metric_document(document)

    def test_missing_incoming_fact_name_raises_metric_definition_error(self):
        existing = worked_example()
        for section in ("dimensions", "measures"):
            with self.subTest(section=section):
                incoming = partial_document(**{section: [{"entity": "customers"}]})
                with self.assertRaises(MetricDefinitionError) as raised:
                    merge_metric_documents(existing, incoming)
                self.assertNotIsInstance(raised.exception, KeyError)

    def test_same_name_entity_and_metric_facts_are_replaced(self):
        existing = worked_example()
        entity = deepcopy(existing["entities"]["customer_totals"])
        entity["source"]["value"] = "SELECT customer_id, SUM(net_amount) AS total_amount FROM paid_orders GROUP BY customer_id"
        metric = deepcopy(existing["metrics"]["Average Customer Revenue"])
        metric["filters"][0]["value"] = "CA"

        merged = merge_metric_documents(
            existing,
            partial_document(
                entities={"CUSTOMER_TOTALS": entity},
                metrics={"average customer revenue": metric},
            ),
        )

        self.assertNotIn("customer_totals", merged["entities"])
        self.assertIn("net_amount", merged["entities"]["CUSTOMER_TOTALS"]["source"]["value"])
        self.assertNotIn("Average Customer Revenue", merged["metrics"])
        self.assertEqual(
            merged["metrics"]["average customer revenue"]["filters"][0]["value"],
            "CA",
        )

    def test_fact_reimport_does_not_overwrite_explicitly_edited_note(self):
        edited = update_metric_notes(
            worked_example(),
            "average customer revenue",
            caveats="Analyst-edited caveat",
        )
        incoming_metric = deepcopy(edited["metrics"]["Average Customer Revenue"])
        incoming_metric["filters"][0]["value"] = "CA"
        incoming = partial_document(
            metrics={"Average Customer Revenue": incoming_metric},
            notes={"Average Customer Revenue": {"caveats": "imported text"}},
        )

        merged = merge_metric_documents(edited, incoming)

        self.assertEqual(
            merged["notes"]["Average Customer Revenue"]["caveats"],
            "Analyst-edited caveat",
        )
        self.assertEqual(
            edited["notes"]["Average Customer Revenue"]["provenance"],
            "analyst_edited",
        )

    def test_system_generated_notes_refresh_on_same_name_reimport(self):
        existing = worked_example()
        existing["notes"]["Average Customer Revenue"].update(
            description="Query A description",
            business_rules="Query A rules",
            provenance="system_generated",
        )
        incoming = worked_example()
        incoming["metrics"]["Average Customer Revenue"]["filters"][0]["value"] = "CA"
        incoming["notes"]["Average Customer Revenue"].update(
            description="Query B description",
            business_rules="Query B rules",
            provenance="system_generated",
        )

        prepared = preserve_analyst_notes(incoming, existing)
        merged = merge_metric_documents(existing, prepared)
        for metric_name, notes in prepared["notes"].items():
            merged = update_metric_notes(merged, metric_name, **notes)

        self.assertEqual(
            merged["notes"]["Average Customer Revenue"]["description"],
            "Query B description",
        )
        self.assertEqual(
            merged["notes"]["Average Customer Revenue"]["business_rules"],
            "Query B rules",
        )
        self.assertEqual(
            merged["notes"]["Average Customer Revenue"]["provenance"],
            "system_generated",
        )

    def test_analyst_edited_notes_survive_same_name_reimport(self):
        existing = update_metric_notes(
            worked_example(),
            "Average Customer Revenue",
            description="Analyst-approved description",
        )
        incoming = worked_example()
        incoming["notes"]["Average Customer Revenue"].update(
            description="Regenerated description",
            provenance="system_generated",
        )

        prepared = preserve_analyst_notes(incoming, existing)

        self.assertEqual(
            prepared["notes"]["Average Customer Revenue"]["description"],
            "Analyst-approved description",
        )
        self.assertEqual(
            prepared["notes"]["Average Customer Revenue"]["provenance"],
            "analyst_edited",
        )

    def test_legacy_notes_without_provenance_are_protected(self):
        existing = worked_example()
        incoming = worked_example()
        incoming["notes"]["Average Customer Revenue"].update(
            description="Regenerated description",
            provenance="system_generated",
        )

        prepared = preserve_analyst_notes(incoming, existing)

        self.assertEqual(
            prepared["notes"]["Average Customer Revenue"]["description"],
            existing["notes"]["Average Customer Revenue"]["description"],
        )
        self.assertNotIn(
            "provenance", prepared["notes"]["Average Customer Revenue"]
        )

    def test_filter_referencing_undeclared_dimension_is_rejected(self):
        document = worked_example()
        document["metrics"]["Average Customer Revenue"]["filters"][0][
            "dimension"
        ] = "customers.missing"

        with self.assertRaisesRegex(MetricDefinitionError, "undeclared dimension"):
            validate_metric_document(document)

    def test_filter_may_reference_declared_entity_key(self):
        document = worked_example()
        document["metrics"]["Average Customer Revenue"]["filters"][0][
            "dimension"
        ] = "customers.id"

        validate_metric_document(document)

    def test_foreign_key_referencing_undeclared_entity_is_rejected(self):
        document = worked_example()
        document["entities"]["paid_orders"]["keys"][1][
            "references_entity"
        ] = "missing"

        with self.assertRaisesRegex(MetricDefinitionError, "undeclared entity"):
            validate_metric_document(document)

    def test_dimension_referencing_undeclared_entity_is_rejected(self):
        document = worked_example()
        document["dimensions"][0]["entity"] = "missing"

        with self.assertRaisesRegex(MetricDefinitionError, "undeclared entity"):
            validate_metric_document(document)

    def test_measure_referencing_undeclared_entity_is_rejected(self):
        document = worked_example()
        document["measures"][0]["entity"] = "missing"

        with self.assertRaisesRegex(MetricDefinitionError, "undeclared entity"):
            validate_metric_document(document)

    def test_yaml_parser_rejects_unresolved_internal_reference(self):
        content = """
entities: {}
dimensions:
  - {name: missing.region, entity: missing, column: region, type: categorical}
measures: []
metrics: {}
notes: {}
"""
        with self.assertRaises(MetricDefinitionError):
            parse_metric_definitions_yaml(content)


def partial_document(**sections):
    document = {
        "entities": {},
        "dimensions": [],
        "measures": [],
        "metrics": {},
        "notes": {},
    }
    document.update(sections)
    return document


def worked_example():
    return {
        "entities": {
            "paid_orders": {
                "source": {
                    "type": "query",
                    "value": "SELECT o.customer_id, o.order_id, o.amount FROM orders o JOIN payments p ON p.order_id = o.order_id AND p.status = 'settled' WHERE o.status = 'complete'",
                },
                "keys": [
                    {"column": "order_id", "type": "primary"},
                    {
                        "column": "customer_id",
                        "type": "foreign",
                        "references_entity": "customers",
                    },
                ],
            },
            "customer_totals": {
                "source": {
                    "type": "query",
                    "value": "SELECT customer_id, SUM(amount) AS total_amount FROM paid_orders GROUP BY customer_id",
                },
                "keys": [{"column": "customer_id", "type": "primary"}],
            },
            "customers": {
                "source": {"type": "table", "value": "customers"},
                "keys": [{"column": "id", "type": "primary"}],
            },
        },
        "dimensions": [
            {
                "name": "customers.region",
                "entity": "customers",
                "column": "region",
                "type": "categorical",
            }
        ],
        "measures": [
            {
                "name": "customer_totals.total_amount_sum",
                "entity": "customer_totals",
                "expression": "SUM(amount)",
                "aggregation": "sum",
                "re_aggregatable": True,
            }
        ],
        "metrics": {
            "Average Customer Revenue": {
                "type": "derived",
                "expression": "AVG(customer_totals.total_amount_sum)",
                "filters": [
                    {
                        "dimension": "customers.region",
                        "operator": "=",
                        "value": "US",
                    }
                ],
            }
        },
        "notes": {
            "Average Customer Revenue": {
                "description": "Average total paid-order revenue per customer, US region only.",
                "business_rules": "Only settled payments and completed orders count toward revenue.",
                "caveats": "",
                "ambiguity_rules": "",
            }
        },
    }


if __name__ == "__main__":
    unittest.main()
