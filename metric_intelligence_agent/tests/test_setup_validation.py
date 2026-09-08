import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from setup_validation import (
    ROW_COUNT_COLUMN,
    deterministic_suggestion,
    empty_validation,
    load_validation,
    save_validation,
    validation_path,
    validation_ready,
)


class SetupValidationTests(unittest.TestCase):
    def test_question_sets_are_isolated_by_source_and_survive_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.duckdb"
            second = Path(directory) / "second.duckdb"
            document = empty_validation(first)
            document["questions"] = [{"id": "q1", "question": "First?"}]
            save_validation(directory, first, document)

            self.assertEqual(load_validation(directory, first)["questions"], document["questions"])
            self.assertEqual(load_validation(directory, second)["questions"], [])
            self.assertNotEqual(
                validation_path(directory, first), validation_path(directory, second)
            )

    def test_save_creates_backup_of_existing_question_set(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.duckdb"
            document = empty_validation(source)
            save_validation(directory, source, document)
            document["questions"] = [{"id": "q1", "question": "Changed?"}]
            path = save_validation(directory, source, document)
            backups = list(path.parent.glob(f"{path.stem}.*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(json.loads(backups[0].read_text())["questions"], [])

    def test_numeric_and_row_count_suggestions(self):
        frame = pd.DataFrame({"revenue": [100.00000001]})
        self.assertTrue(deterministic_suggestion(frame, "revenue", "100")[0])
        self.assertTrue(deterministic_suggestion(frame, ROW_COUNT_COLUMN, "1")[0])
        self.assertFalse(deterministic_suggestion(frame, "missing", "1")[0])
        self.assertIsNone(deterministic_suggestion(frame, "revenue", "")[0])

    def test_readiness_requires_system_success_and_analyst_pass_for_every_question(self):
        document = {
            "questions": [{"id": "one"}, {"id": "two"}],
            "results": {
                "one": {"system_success": True, "analyst_decision": "pass"},
                "two": {"system_success": True, "analyst_decision": "pass"},
            },
        }
        self.assertTrue(validation_ready(document))
        document["results"]["two"]["analyst_decision"] = "fail"
        self.assertFalse(validation_ready(document))

    def test_shared_runner_can_skip_eval_logging_for_setup_validation(self):
        from eval.runner import run_question

        terminal = {
            "question": "How many?", "sql": "select 1", "out_of_range": False,
            "attempt": 1, "success": True, "data": pd.DataFrame({"count": [1]}),
            "error": "", "valid": True, "validation_reason": "ok",
            "explanation": "One.", "total_input_tokens": 1,
            "total_output_tokens": 1,
        }

        class FakeGraph:
            def stream(self, _state, stream_mode):
                self.stream_mode = stream_mode
                yield terminal

        with patch("eval.runner.log_run") as log_run:
            state, run_id = run_question(
                FakeGraph(), None, "How many?", verbose=False, log=False
            )
        self.assertIs(state, terminal)
        self.assertIsNone(run_id)
        log_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
