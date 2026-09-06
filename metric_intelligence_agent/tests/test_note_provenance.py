import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "test-only-placeholder")

from app_pages.setup import _preserve_saved_notes
from metric_import import (
    protected_note_reimports,
    save_metric_definitions_yaml,
    update_metric_notes,
)
from sql_semantic_import import extract_sql_semantic_document


QUERY_A = "SELECT SUM(amount) AS revenue FROM orders WHERE status = 'completed'"
QUERY_B = "SELECT SUM(amount) AS revenue FROM orders WHERE status = 'pending'"


class NoteProvenanceTests(unittest.TestCase):
    def test_analyst_edited_note_is_reported_for_reimport(self):
        existing = update_metric_notes(
            imported_document(QUERY_A, "Generated description"),
            "revenue",
            description="Analyst description",
        )
        incoming = imported_document(QUERY_B, "Regenerated description")

        self.assertEqual(protected_note_reimports(incoming, existing), ["revenue"])

    def test_system_generated_note_is_not_reported_for_reimport(self):
        existing = imported_document(QUERY_A, "Generated description")
        incoming = imported_document(QUERY_B, "Regenerated description")

        self.assertEqual(protected_note_reimports(incoming, existing), [])

    def test_legacy_protected_note_is_reported_for_reimport(self):
        existing = imported_document(QUERY_A, "Existing description")
        existing["notes"]["revenue"].pop("provenance")
        incoming = imported_document(QUERY_B, "Regenerated description")

        self.assertEqual(protected_note_reimports(incoming, existing), ["revenue"])

    def test_system_generated_notes_refresh_when_sql_facts_change(self):
        first = imported_document(QUERY_A, "Completed-order revenue")
        second = imported_document(QUERY_B, "Pending-order revenue")

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "metric_definitions.yaml"
            save_metric_definitions_yaml(path, first)
            prepared = _preserve_saved_notes(second, path)

        self.assertNotEqual(
            first["metrics"]["revenue"]["filters"],
            prepared["metrics"]["revenue"]["filters"],
        )
        self.assertEqual(
            prepared["notes"]["revenue"]["description"],
            "Pending-order revenue",
        )

    def test_manual_edit_protects_notes_when_sql_facts_change(self):
        first = update_metric_notes(
            imported_document(QUERY_A, "Completed-order revenue"),
            "revenue",
            description="Analyst-approved revenue definition",
        )
        second = imported_document(QUERY_B, "Pending-order revenue")

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "metric_definitions.yaml"
            save_metric_definitions_yaml(path, first)
            prepared = _preserve_saved_notes(second, path)

        self.assertEqual(
            prepared["notes"]["revenue"]["description"],
            "Analyst-approved revenue definition",
        )
        self.assertEqual(
            prepared["notes"]["revenue"]["provenance"], "analyst_edited"
        )

    def test_existing_notes_without_provenance_remain_protected(self):
        first = imported_document(QUERY_A, "Existing saved description")
        first["notes"]["revenue"].pop("provenance")
        second = imported_document(QUERY_B, "Regenerated description")

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "metric_definitions.yaml"
            save_metric_definitions_yaml(path, first)
            prepared = _preserve_saved_notes(second, path)

        self.assertEqual(
            prepared["notes"]["revenue"]["description"],
            "Existing saved description",
        )
        self.assertNotIn("provenance", prepared["notes"]["revenue"])


def imported_document(sql, description):
    document = extract_sql_semantic_document(sql, "revenue")["document"]
    document["notes"]["revenue"] = {
        "description": description,
        "business_rules": "",
        "caveats": "",
        "ambiguity_rules": "",
        "provenance": "system_generated",
    }
    return document


if __name__ == "__main__":
    unittest.main()
