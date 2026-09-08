import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from setup_validation import (
    empty_validation,
    invalidate_readiness,
    load_validation,
    save_validation,
    source_key,
)


def validation_test_app(context_dir, initial_source):
    from pathlib import Path

    import pandas as pd
    import streamlit as st

    from app_pages.validate import show

    selected_source = st.text_input(
        "Harness source", value=initial_source, key="harness_source"
    )
    st.session_state.setup_source_path = selected_source
    st.session_state.custom_conn = True
    st.session_state.setdefault("eval_conn", object())

    def executor(question):
        counts = st.session_state.setdefault("executor_counts", {})
        counts[question] = counts.get(question, 0) + 1
        return {
            "sql": f"SELECT 1 AS answer -- {question}",
            "data": pd.DataFrame({"answer": [1]}),
            "explanation": f"Answer for {question}",
            "error": "",
            "attempt": 1,
            "success": True,
            "valid": True,
            "out_of_range": False,
        }

    events = st.session_state.setdefault("validation_status_events", [])
    show(Path(context_dir), executor=executor, status_events=events)


class ValidateUiTests(unittest.TestCase):
    def _app(self, context_dir, source):
        return AppTest.from_function(
            validation_test_app,
            args=(str(context_dir), str(source)),
            default_timeout=10,
        ).run()

    def _save_questions(self, app, source, questions):
        prefix = source_key(source)
        app.number_input(key=f"{prefix}_count").set_value(len(questions))
        app.run()
        for index, question in enumerate(questions):
            app.text_input(key=f"{prefix}_question_{index}").set_value(question)
        app.button(key=f"{prefix}_save_questions").click().run()
        return prefix

    def test_questions_survive_new_app_instance_and_are_isolated_by_source(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.duckdb"
            second = Path(directory) / "second.duckdb"
            app = self._app(directory, first)
            first_prefix = self._save_questions(app, first, ["Saved question?"])

            restarted = self._app(directory, first)
            self.assertEqual(
                restarted.text_input(key=f"{first_prefix}_question_0").value,
                "Saved question?",
            )
            restarted.text_input(key="harness_source").set_value(str(second)).run()
            second_prefix = source_key(second)
            self.assertEqual(
                restarted.text_input(key=f"{second_prefix}_question_0").value, ""
            )

    def test_single_and_failed_reruns_execute_only_selected_questions(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.duckdb"
            app = self._app(directory, source)
            prefix = self._save_questions(app, source, ["First?", "Second?"])
            document = load_validation(directory, source)
            first_id, second_id = [q["id"] for q in document["questions"]]

            app.button(key=f"{prefix}_run_{first_id}").click().run()
            self.assertEqual(dict(app.session_state["executor_counts"]), {"First?": 1})
            app.button(key=f"{prefix}_fail_{first_id}").click().run()
            app.button(key=f"{prefix}_run_failed").click().run()
            self.assertEqual(dict(app.session_state["executor_counts"]), {"First?": 2})
            self.assertNotIn("Second?", app.session_state["executor_counts"])
            self.assertTrue(app.button(key=f"{prefix}_run_{second_id}").disabled is False)

    def test_each_question_records_queued_running_completed_statuses(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.duckdb"
            app = self._app(directory, source)
            prefix = self._save_questions(app, source, ["First?", "Second?"])
            ids = [q["id"] for q in load_validation(directory, source)["questions"]]
            app.button(key=f"{prefix}_run_all").click().run()
            events = list(app.session_state["validation_status_events"])
            for question_id in ids:
                self.assertEqual(
                    [e["status"] for e in events if e["question_id"] == question_id],
                    ["queued", "running", "completed"],
                )

    def test_run_controls_are_disabled_in_flight_and_do_not_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.duckdb"
            app = self._app(directory, source)
            prefix = self._save_questions(app, source, ["Only once?"])
            question_id = load_validation(directory, source)["questions"][0]["id"]
            app.session_state["validation_in_flight"] = True
            app.run()
            self.assertTrue(app.button(key=f"{prefix}_run_all").disabled)
            self.assertTrue(app.button(key=f"{prefix}_run_{question_id}").disabled)
            app.button(key=f"{prefix}_run_all").click().run()
            self.assertNotIn("executor_counts", app.session_state)

    def test_stale_signal_clears_and_returns_after_rerun_and_analyst_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.duckdb"
            document = empty_validation(source)
            document["questions"] = [{
                "id": "known", "question": "Known?",
                "expected_column": "answer", "expected_value": "1",
            }]
            document["results"] = {
                "known": {
                    "system_success": True,
                    "analyst_decision": "pass",
                    "detected_layer": "fact",
                    "attempt": 1,
                    "suggestion": True,
                    "suggestion_reason": "matches",
                    "failure_origin": None,
                }
            }
            document["ready"] = True
            save_validation(directory, source, document)
            prefix = source_key(source)

            app = self._app(directory, source)
            self.assertTrue(any("Ready for Query mode" in item.value for item in app.success))
            invalidate_readiness(directory, source)
            app.run()
            self.assertTrue(any("Readiness is stale" in item.value for item in app.warning))
            self.assertFalse(load_validation(directory, source)["ready"])

            app.button(key=f"{prefix}_run_known").click().run()
            app.button(key=f"{prefix}_pass_known").click().run()
            self.assertTrue(any("Ready for Query mode" in item.value for item in app.success))
            self.assertTrue(load_validation(directory, source)["ready"])

    def test_activate_button_switches_source_without_reexecuting_app(self):
        with tempfile.TemporaryDirectory() as directory:
            context_dir = Path(directory)
            source = context_dir / "source.duckdb"
            (context_dir / "table_catalog.md").write_text(
                "# Catalog\nTest table", encoding="utf-8"
            )
            (context_dir / "schema.sql").write_text(
                "CREATE TABLE test_table (id INTEGER);", encoding="utf-8"
            )
            (context_dir / "metric_definitions.md").write_text(
                "# Metrics\nNo metrics required for this state test.", encoding="utf-8"
            )
            document = empty_validation(source)
            document["questions"] = [{
                "id": "ready", "question": "Ready?",
                "expected_column": "", "expected_value": "",
            }]
            document["results"] = {
                "ready": {"system_success": True, "analyst_decision": "pass"}
            }
            document["ready"] = True
            save_validation(context_dir, source, document)

            app = self._app(context_dir, source)
            app.session_state["messages"] = [{"role": "user"}]
            app.session_state["query_cache"] = {"cached": True}
            app.session_state["last_run_id"] = "old"
            app.button(key=f"{source_key(source)}_activate").click().run()

            self.assertEqual(list(app.exception), [])
            self.assertEqual(app.session_state["data_source"], "custom")
            self.assertIs(app.session_state["conn"], app.session_state["custom_conn"])
            self.assertEqual(list(app.session_state["messages"]), [])
            self.assertEqual(dict(app.session_state["query_cache"]), {})
            self.assertIsNone(app.session_state["last_run_id"])
            self.assertIn("=== TABLE CATALOG ===", app.session_state["context"])


if __name__ == "__main__":
    unittest.main()
