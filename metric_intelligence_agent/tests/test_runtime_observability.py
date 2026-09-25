import json

import pytest

import graph
from eval.logger import log_run, setup_eval_db
from eval.runner import run_question
from layer_reporting import detect_layer_used, load_table_layers
from model_config import resolve_model
from test_graph_retry import ConnectionSpy, ScriptedModel, USAGE


@pytest.mark.parametrize("configured,expected", [(None, "gpt-4o"), ("", "gpt-4o"), (" another-model ", "another-model")])
def test_model_snapshot_all_stages(monkeypatch, configured, expected):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    if configured is not None:
        monkeypatch.setenv("OPENAI_MODEL", configured)
    assert resolve_model() == expected
    client = ScriptedModel([("generate", "SELECT * FROM alpha"), ("reflect", "SELECT * FROM alpha"),
                            ("validate", "VALID: yes"), ("explain", "answer")], [])
    calls = []
    create = client.chat.completions.create

    def capture(**kwargs):
        calls.append((kwargs["model"], kwargs["temperature"]))
        return create(**kwargs)

    client.chat.completions.create = capture
    mapping = {"alpha": "Aggregated"}
    runtime = graph.build_graph(ConnectionSpy(["tech", "yes"], []), "context", client, table_layers=mapping)
    mapping["alpha"] = "Fact"
    monkeypatch.setenv("OPENAI_MODEL", "changed-after-build")
    state, _ = run_question(runtime, None, "q", log=False, verbose=False)
    assert calls == [(expected, 0)] * 4
    assert state["model"] == expected
    assert state["layer_used"] == "aggregated"
    conn = setup_eval_db(":memory:")
    try:
        log_run(conn, state, 1)
        assert conn.execute("SELECT model, layer_used FROM query_log").fetchone() == (expected, "aggregated")
    finally:
        conn.close()


@pytest.mark.parametrize("outcomes", [["yes"], ["tech", "yes"], ["no", "yes"],
                                     ["bad", "yes"], ["tech"] * 3, ["no"] * 3,
                                     ["bad"] * 3, ["empty"] * 3, []])
def test_trace_and_persisted_accounting(monkeypatch, outcomes):
    monkeypatch.setattr(graph, "MAX_ATTEMPTS", 3)
    script = [("generate", "SELECT 1" if outcomes else "OUT_OF_RANGE: dates")]
    for index, outcome in enumerate(outcomes):
        if index:
            script.append(("reflect", f"SELECT {index + 1}"))
        if outcome in ("yes", "no", "bad"):
            script.append(("validate", {"yes": "VALID: yes", "no": "VALID: no\nREASON: Wrong metric", "bad": "Unclear"}[outcome]))
    verified = bool(outcomes and outcomes[-1] == "yes")
    if verified:
        script.append(("explain", "answer"))
    state, _ = run_question(graph.build_graph(ConnectionSpy(outcomes, []), "context", ScriptedModel(script, [])),
                            None, "q", log=False, verbose=False)
    trace = state["attempt_trace"]
    assert len(trace) == len(outcomes)
    for index, (entry, outcome) in enumerate(zip(trace, outcomes)):
        assert entry["attempt"] == index + 1
        assert entry["stage"] == ("initial" if index == 0 else "correction")
        assert entry["sql"] == f"SELECT {index + 1}"
        assert entry["execution_success"] == (outcome != "tech")
        assert bool(entry["execution_error"]) == (outcome == "tech")
        assert entry["row_count"] == (None if outcome == "tech" else 0 if outcome == "empty" else 1)
        reviewed = outcome not in ("tech", "empty")
        assert entry["semantic_review_ran"] == reviewed
        assert entry["validation_pass"] == (None if outcome == "tech" else outcome == "yes")
        assert bool(entry["validation_reason"]) == (outcome in ("no", "bad", "empty"))
        if outcome == "bad":
            assert "invalid or unrecognized" in entry["validation_reason"]
        assert entry["generation_usage"] == dict(zip(("input_tokens", "output_tokens"), USAGE["reflect" if index else "generate"]))
        assert entry["validation_usage"] == {"input_tokens": 30 if reviewed else 0, "output_tokens": 3 if reviewed else 0}
    for kind in ("input_tokens", "output_tokens"):
        assert sum(entry[stage][kind] for entry in trace for stage in ("generation_usage", "validation_usage")) + state["non_attempt_usage"][kind] == state["total_" + kind]
    conn = setup_eval_db(":memory:")
    try:
        run_id = log_run(conn, state, 1)
        model, stored, layer = conn.execute("SELECT model, attempt_trace, layer_used FROM query_log WHERE run_id=?", [run_id]).fetchone()
        assert model == state["model"]
        assert json.loads(stored) == trace
        assert layer == state["layer_used"]
    finally:
        conn.close()


def test_legacy_database_migration_idempotent(tmp_path):
    path = tmp_path / "old.db"
    conn = setup_eval_db(path)
    conn.execute("ALTER TABLE query_log DROP COLUMN model")
    conn.execute("ALTER TABLE query_log DROP COLUMN attempt_trace")
    conn.execute("INSERT INTO query_log (run_id, question, human_rating) VALUES ('old', 'keep me', 'correct')")
    conn.close()
    for _ in range(2):
        conn = setup_eval_db(path)
        assert conn.execute("SELECT run_id, question, human_rating, model, attempt_trace FROM query_log").fetchall() == [("old", "keep me", "correct", None, None)]
        conn.close()


@pytest.mark.parametrize("sql,expected", [
    ("SELECT * FROM alpha", "aggregated"), ("SELECT * FROM beta", "fact"),
    ("SELECT * FROM gamma", "dimension"),
    ("SELECT * FROM alpha JOIN beta ON true JOIN gamma ON true", "aggregated"),
    ("SELECT * FROM beta JOIN gamma ON true", "fact"),
    ("SELECT * FROM missing", "unknown"),
    ("SELECT * FROM alpha JOIN missing ON true", "unknown"),
    ("SELECT 'alpha' FROM gamma -- beta", "dimension"),
    ("WITH alpha AS (SELECT * FROM gamma) SELECT * FROM alpha", "dimension"),
    ("WITH alpha AS (SELECT 1) SELECT * FROM alpha", "unknown"),
    ("SELECT * FROM main.alpha", "aggregated"),
    ("SELECT * FROM other.alpha", "unknown"),
    ("SELECT * FROM delta", "unknown"), ("SELECT * FROM epsilon", "unknown"),
    ("SELECT * FROM read_csv('alpha')", "unknown"), ("invalid sql!", "unknown"),
])
def test_authoritative_layers(sql, expected):
    mapping = {"alpha": "Aggregated", "beta": "Fact", "gamma": "Dimension", "delta": "Bridge", "epsilon": "Skip"}
    before = dict(mapping)
    assert detect_layer_used(sql, mapping) == expected
    assert mapping == before


def test_mapping_sources(tmp_path):
    assert load_table_layers(tmp_path) == {}
    assert detect_layer_used("SELECT * FROM orders") == "unknown"
    (tmp_path / "layer_classifications.json").write_text(json.dumps({"orders": "Aggregated", "odd_name": "Fact"}))
    custom = load_table_layers(tmp_path)
    assert detect_layer_used("SELECT * FROM orders", custom) == "aggregated"
    assert detect_layer_used("SELECT * FROM odd_name", custom) == "fact"
    demo = load_table_layers(demo=True)
    for name, expected in {"agg_daily_sales": "aggregated", "agg_monthly_sales": "aggregated", "orders": "fact", "lineitem": "fact", "customer": "dimension", "supplier": "dimension", "nation": "dimension", "region": "dimension"}.items():
        assert detect_layer_used(f"SELECT * FROM {name}", demo) == expected
    assert detect_layer_used("SELECT * FROM x", {"X": "fact", "x": "dimension"}) == "unknown"
