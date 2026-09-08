import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from metric_import import load_metric_definitions_yaml, save_metric_definitions_yaml
from setup_validation import empty_validation, load_validation, save_validation


def metric_deletion_app(metric_path, source_path):
    from pathlib import Path

    import streamlit as st

    from app_pages.setup import _render_manual_review

    st.session_state.setup_source_path = source_path
    _render_manual_review(Path(metric_path))


class MetricDeletionUiTests(unittest.TestCase):
    def _document(self, shared=False):
        metrics = {
            "revenue": {
                "type": "simple",
                "measure": "orders.revenue",
                "filters": [],
            }
        }
        notes = {
            "revenue": {
                "description": "Revenue",
                "business_rules": "",
                "caveats": "",
                "ambiguity_rules": "",
            }
        }
        if shared:
            metrics["revenue_copy"] = {
                "type": "derived",
                "expression": "AVG(orders.revenue)",
                "filters": [],
            }
            notes["revenue_copy"] = {
                "description": "Shared consumer",
                "business_rules": "",
                "caveats": "",
                "ambiguity_rules": "",
            }
        return {
            "entities": {
                "orders": {
                    "source": {"type": "table", "value": "orders"},
                    "keys": [{"column": "id", "type": "primary"}],
                }
            },
            "dimensions": [],
            "measures": [{
                "name": "orders.revenue",
                "entity": "orders",
                "expression": "SUM(amount)",
                "aggregation": "sum",
                "re_aggregatable": True,
            }],
            "metrics": metrics,
            "notes": notes,
        }

    def _ready_validation(self, directory, source):
        validation = empty_validation(source)
        validation["questions"] = [{"id": "q", "question": "Ready?"}]
        validation["results"] = {
            "q": {"system_success": True, "analyst_decision": "pass"}
        }
        validation["ready"] = True
        save_validation(directory, source, validation)

    def _run_delete(self, shared):
        temporary = tempfile.TemporaryDirectory()
        directory = Path(temporary.name)
        source = directory / "source.duckdb"
        metric_path = directory / "metric_definitions.yaml"
        save_metric_definitions_yaml(metric_path, self._document(shared=shared))
        self._ready_validation(directory, source)
        app = AppTest.from_function(
            metric_deletion_app,
            args=(str(metric_path), str(source)),
            default_timeout=10,
        ).run()
        if shared:
            self.assertTrue(
                any(
                    "Shared dependencies will be preserved" in item.value
                    for item in app.info
                )
            )
        delete_key = "manual_metric_delete_revenue"
        self.assertTrue(app.button(key=delete_key).disabled)
        app.checkbox(key="manual_metric_delete_confirm_revenue").check().run()
        self.assertFalse(app.button(key=delete_key).disabled)
        app.button(key=delete_key).click().run()
        return temporary, directory, source, metric_path, app

    def test_standalone_metric_delete_updates_yaml_backup_and_readiness(self):
        temporary, directory, source, metric_path, app = self._run_delete(False)
        try:
            document = load_metric_definitions_yaml(metric_path)
            self.assertNotIn("revenue", document["metrics"])
            self.assertNotIn("revenue", document["notes"])
            self.assertEqual(document["measures"], [])
            self.assertEqual(document["entities"], {})
            self.assertEqual(len(list(directory.glob("metric_definitions.*.bak"))), 1)
            validation = load_validation(directory, source)
            self.assertFalse(validation["ready"])
            self.assertTrue(validation["stale"])
            self.assertTrue(any("Deleted metric revenue" in item.value for item in app.success))
        finally:
            temporary.cleanup()

    def test_shared_dependency_is_surfaced_and_preserved(self):
        temporary, directory, source, metric_path, app = self._run_delete(True)
        try:
            document = load_metric_definitions_yaml(metric_path)
            self.assertNotIn("revenue", document["metrics"])
            self.assertIn("revenue_copy", document["metrics"])
            self.assertEqual([m["name"] for m in document["measures"]], ["orders.revenue"])
            validation = load_validation(directory, source)
            self.assertFalse(validation["ready"])
            self.assertTrue(validation["stale"])
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
