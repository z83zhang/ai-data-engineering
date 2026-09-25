from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

import agent


@pytest.mark.parametrize("content,valid,reason", [
    ("VALID: yes", True, ""),
    ("VALID: no\nREASON: Wrong metric.", False, "Wrong metric."),
    (" \n valid : YES \t\n", True, ""),
    (" valid: NO\r\n\r\n reason : Wrong join. \t", False, "Wrong join."),
])
def test_recognized_decisions(content, valid, reason):
    assert agent.parse_validation_response(content) == (valid, reason)


@pytest.mark.parametrize("content", [
    "I cannot determine whether this is correct.", "", " \n", None,
    "NOT VALID: yes", "This contains VALID: yes in prose.",
    "VALID: yes\nBut I cannot verify this.", "VALID: yes\nVALID: no\nREASON: wrong",
    "VALID: no\nREASON: wrong\nVALID: yes", "VALID: maybe", "VALID: no",
    "VALID: no\nREASON:", "VALID: no\nREASON:   ",
    "VALID: no\nREASON: VALID: yes", "VALID: yes\nREASON: wrong",
    "```\nVALID: yes\n```", "VALID: yes yes", "VALID:\nyes",
])
def test_unrecognized_or_ambiguous_decisions_fail_safely(content):
    valid, reason = agent.parse_validation_response(content)
    assert valid is False
    assert "invalid or unrecognized response" in reason


@pytest.mark.parametrize("content", ["VALID: yes", "VALID: no\nREASON: Wrong grain.", "prose", "", None])
def test_validation_call_retains_usage_even_for_invalid_response(content):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=37, completion_tokens=11),
    )
    create = Mock(return_value=response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = agent.validate_result("q", "SELECT 1", pd.DataFrame({"x": [1]}), "context", client)
    assert (result["valid"], result["reason"]) == agent.parse_validation_response(content)
    assert (result["input_tokens"], result["output_tokens"]) == (37, 11)
    create.assert_called_once()


@pytest.mark.parametrize("data", [pd.DataFrame({"x": []}), pd.DataFrame({"x": [float("nan")]})])
def test_empty_and_all_null_results_still_fail_without_model_call(data):
    create = Mock(side_effect=AssertionError("No model call allowed"))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = agent.validate_result("q", "SELECT 1", data, "context", client)
    assert result["valid"] is False
    assert result["reason"]
    assert (result["input_tokens"], result["output_tokens"]) == (0, 0)
    create.assert_not_called()


def test_reflection_only_rewrites_and_never_executes(monkeypatch):
    execute = Mock(side_effect=AssertionError("Reflection must not execute SQL"))
    monkeypatch.setattr(agent, "run_sql", execute)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="```sql\nSELECT 2\n```"))],
        usage=SimpleNamespace(prompt_tokens=17, completion_tokens=5),
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=response))))
    assert agent.reflect_sql("context", "q", "SELECT bad", "bad column", client) == {
        "sql": "SELECT 2", "input_tokens": 17, "output_tokens": 5,
    }
    execute.assert_not_called()
