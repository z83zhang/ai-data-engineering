"""Exercise the real graph, runner, SQL executor, and agent calls with fake I/O."""

from collections import Counter
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

import graph
import eval.runner as runner
from utils import compute_cost


USAGE = {"generate": (10, 1), "reflect": (20, 2), "validate": (30, 3), "explain": (40, 4)}


class ScriptedModel:
    def __init__(self, script, events):
        self.script = list(script)
        self.calls = []
        self.events = events
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        system = kwargs["messages"][0]["content"]
        role = ("generate" if "writes SQL" in system else
                "reflect" if "fixes SQL" in system else
                "validate" if "data quality validator" in system else "explain")
        assert self.script, f"Unexpected extra model call: {role}"
        expected, content = self.script.pop(0)
        assert role == expected
        self.calls.append(role)
        self.events.append(role)
        incoming, outgoing = USAGE[role]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=incoming, completion_tokens=outgoing),
        )


class ConnectionSpy:
    def __init__(self, outcomes, events):
        self.outcomes = outcomes
        self.executions = []
        self.events = events

    def execute(self, sql):
        self.executions.append(sql)
        self.events.append("execute")
        outcome = self.outcomes[len(self.executions) - 1]
        if outcome == "tech":
            raise RuntimeError("SQL execution failed")
        return SimpleNamespace(df=lambda: pd.DataFrame({"answer": [] if outcome == "empty" else [1]}))


# Expected totals are explicit and independent of graph/runner accounting.
@pytest.mark.parametrize("outcomes,verified,tokens,roles", [
    (["yes"], True, 80, ["generate", "validate", "explain"]),
    (["tech", "yes"], True, 100, ["generate", "reflect", "validate", "explain"]),
    (["no", "yes"], True, 130, ["generate", "validate", "reflect", "validate", "explain"]),
    (["bad", "yes"], True, 130, ["generate", "validate", "reflect", "validate", "explain"]),
    (["tech", "tech", "yes"], True, 120, ["generate", "reflect", "reflect", "validate", "explain"]),
    (["no", "no", "yes"], True, 180, ["generate", "validate", "reflect", "validate", "reflect", "validate", "explain"]),
    (["tech", "tech", "tech"], False, 50, ["generate", "reflect", "reflect"]),
    (["no", "no", "no"], False, 140, ["generate", "validate", "reflect", "validate", "reflect", "validate"]),
    (["bad", "bad", "bad"], False, 140, ["generate", "validate", "reflect", "validate", "reflect", "validate"]),
    (["no", "tech", "bad"], False, 110, ["generate", "validate", "reflect", "reflect", "validate"]),
    (["no", "no", "tech"], False, 110, ["generate", "validate", "reflect", "validate", "reflect"]),
    (["empty", "empty", "empty"], False, 50, ["generate", "reflect", "reflect"]),
], ids=["initial-success", "technical-retry", "semantic-retry", "malformed-retry",
        "two-technical-retries", "two-semantic-retries", "technical-exhaustion",
        "semantic-exhaustion", "malformed-exhaustion", "mixed-malformed-final",
        "mixed-technical-final", "empty-stays-invalid"])
def test_execution_routing_and_exact_terminal_accounting(monkeypatch, outcomes, verified, tokens, roles):
    monkeypatch.setattr(graph, "MAX_ATTEMPTS", 3)
    events, script = [], [("generate", "SELECT 1")]
    for index, outcome in enumerate(outcomes):
        if index:
            script.append(("reflect", f"SELECT {index + 1}"))
        if outcome not in ("tech", "empty"):
            script.append(("validate", {"yes": "VALID: yes", "no": "VALID: no\nREASON: Wrong metric", "bad": "Cannot determine correctness"}[outcome]))
    if verified:
        script.append(("explain", "Verified answer"))
    model = ScriptedModel(script, events)
    connection = ConnectionSpy(outcomes, events)
    validate = Mock(wraps=graph.validate_result)
    monkeypatch.setattr(graph, "validate_result", validate)
    cost = Mock(wraps=compute_cost)
    monkeypatch.setattr(graph, "compute_cost", cost)
    fallback = Mock(side_effect=AssertionError("Graph must supply terminal cost"))
    monkeypatch.setattr(runner, "compute_cost", fallback)
    log = Mock(return_value="run-id")
    monkeypatch.setattr(runner, "log_run", log)

    terminal, run_id = runner.run_question(
        graph.build_graph(connection, "context", model), None, "q", verbose=False,
    )

    assert run_id == "run-id"
    assert terminal["attempt"] == len(outcomes) == len(connection.executions)
    assert len(connection.executions) <= 3
    assert connection.executions == [f"SELECT {i + 1}" for i in range(len(outcomes))]
    assert validate.call_count == sum(outcome != "tech" for outcome in outcomes)
    assert model.calls == roles and model.script == []
    assert Counter(model.calls)["explain"] == int(verified)
    assert terminal["explanation"] == ("Verified answer" if verified else "")
    assert terminal["valid"] is verified
    assert terminal["success"] is (outcomes[-1] != "tech")
    assert terminal["total_input_tokens"] == tokens
    assert terminal["total_output_tokens"] == tokens // 10
    assert terminal["cost_usd"] == compute_cost(tokens, tokens // 10)
    cost.assert_called_once_with(tokens, tokens // 10)
    fallback.assert_not_called()
    assert log.call_args.args[1] is terminal
    if not verified:
        assert terminal["error"]
    if outcomes[-1] == "bad":
        assert "invalid or unrecognized" in terminal["error"]
    if verified:
        assert terminal["error"] == ""
    # Exact ordering prevents reflection or any other node owning a second execution.
    expected_events = ["generate"]
    for index, outcome in enumerate(outcomes):
        if index:
            expected_events.append("reflect")
        expected_events.append("execute")
        if outcome not in ("tech", "empty"):
            expected_events.append("validate")
    if verified:
        expected_events.append("explain")
    assert events == expected_events


def test_out_of_range_has_zero_executions_and_generation_cost(monkeypatch):
    events = []
    model = ScriptedModel([("generate", "OUT_OF_RANGE: demo dates")], events)
    connection = ConnectionSpy([], events)
    monkeypatch.setattr(runner, "compute_cost", Mock(side_effect=AssertionError("No fallback")))
    terminal, _ = runner.run_question(graph.build_graph(connection, "context", model), None, "q", verbose=False, log=False)
    assert events == ["generate"]
    assert terminal["attempt"] == 0 and connection.executions == []
    assert terminal["out_of_range"] is True
    assert terminal["success"] is False and terminal["valid"] is False
    assert terminal["data"] is None and terminal["explanation"] == ""
    assert (terminal["total_input_tokens"], terminal["total_output_tokens"]) == (10, 1)
    assert terminal["cost_usd"] == compute_cost(10, 1)


@pytest.mark.parametrize("outcome", ["tech", "bad", "no"])
def test_single_attempt_budget_never_reflects(monkeypatch, outcome):
    monkeypatch.setattr(graph, "MAX_ATTEMPTS", 1)
    events = []
    script = [("generate", "SELECT 1")]
    if outcome != "tech":
        script.append(("validate", "bad" if outcome == "bad" else "VALID: no\nREASON: wrong"))
    model = ScriptedModel(script, events)
    connection = ConnectionSpy([outcome], events)
    terminal, _ = runner.run_question(graph.build_graph(connection, "context", model), None, "q", verbose=False, log=False)
    assert terminal["attempt"] == len(connection.executions) == 1
    assert "reflect" not in events and "explain" not in events
    incoming = 10 if outcome == "tech" else 40
    assert (terminal["total_input_tokens"], terminal["total_output_tokens"]) == (incoming, incoming // 10)
    assert terminal["cost_usd"] == compute_cost(incoming, incoming // 10)
    assert terminal["valid"] is False and terminal["error"]


def test_invalid_budget_rejected_before_any_calls(monkeypatch):
    monkeypatch.setattr(graph, "MAX_ATTEMPTS", 0)
    with pytest.raises(ValueError, match="at least one SQL execution"):
        graph.build_graph(None, "context")
