import copy
import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from connectors.duckdb import DuckDBConnector
from eval import custom_golden, run_custom_eval
from eval.custom_suite import ContractError, digest, inputs, load_suite
from eval.logger import setup_eval_db
from test_graph_retry import ScriptedModel


@pytest.fixture
def package(tmp_path):
    db = tmp_path / "tiny.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute("CREATE TABLE zebra(month INTEGER, amount DECIMAL(8,3))")
    conn.execute("INSERT INTO zebra VALUES (1, 12.345), (2, 20.000)")
    conn.close()
    context = tmp_path / "context"
    context.mkdir()
    for name, content in {
        "schema.sql": "CREATE TABLE zebra(month INTEGER, amount DECIMAL(8,3));",
        "table_catalog.md": "zebra is an analyst-classified aggregate by month.",
        "metric_definitions.md": "Revenue = SUM(amount), grouped by month; exclusions: none.",
        "layer_classifications.json": json.dumps({"zebra": "Aggregated"}),
    }.items():
        (context / name).write_text(content, encoding="utf-8")
    case = {"id": "monthly", "question": "Revenue by month?", "expected_layer": "aggregated",
            "result": {"keys": {"month": {"type": "month", "aliases": ["period"]}},
                       "values": {"revenue": {"type": "number", "aliases": ["sales"], "abs_tol": 0.005, "rel_tol": 0}}},
            "reference": {"kind": "sql", "sql": "SELECT month, SUM(amount) AS revenue FROM zebra GROUP BY month"},
            "metric": {"definition": "metric_definitions.md: Revenue", "formula": "SUM(amount)", "exclusions": "none", "grain": ["month"]},
            "tolerance_rationale": "Two-decimal reporting", "notes": "Synthetic mechanism test, not jaffle_shop"}
    template = tmp_path / "template.json"
    template.write_text(json.dumps({"version": 1, "source_id": "synthetic-only", "cases": [case]}))
    suite_path = tmp_path / "reviewed.json"
    candidate = custom_golden.candidate(db, context, template, suite_path)
    assert candidate["approval"]["status"] == "requires_owner_review"
    candidate["approval"] = {"status": "approved", "reviewer": "test-owner", "note": "Synthetic reference independently specified in test"}
    suite_path.write_text(json.dumps(candidate))
    return db, context, suite_path, candidate


def test_valid_package_freshness_and_read_only(package, monkeypatch):
    db, context, path, suite = package
    monkeypatch.setattr("agent.get_openai_client", lambda: pytest.fail("Freshness must not call OpenAI"))
    assert inputs(db, context, path)[0] == suite
    assert custom_golden.check(db, context, path) == [{"id": "monthly", "passed": True, "reasons": []}]
    conn = DuckDBConnector().connect({"path": db})
    try:
        with pytest.raises(duckdb.InvalidInputException):
            conn.execute("DELETE FROM zebra")
    finally:
        conn.close()


@pytest.mark.parametrize("field", ["db", "context", "suite"])
def test_missing_inputs(package, tmp_path, field):
    db, context, path, _ = package
    args = [db, context, path]
    args[["db", "context", "suite"].index(field)] = tmp_path / "missing"
    with pytest.raises((ContractError, FileNotFoundError), match="missing|Cannot load"):
        inputs(*args)


@pytest.mark.parametrize("mutation", [
    lambda s: s["cases"].append(copy.deepcopy(s["cases"][0])),
    lambda s: s["cases"][0].pop("question"),
    lambda s: s["cases"][0]["result"]["keys"]["month"].update(type="bogus"),
    lambda s: s["cases"][0]["result"]["values"]["revenue"].update(abs_tol=-1),
    lambda s: s["cases"][0]["result"]["values"]["revenue"].update(type="integer", abs_tol=0.005),
    lambda s: s["cases"][0]["result"]["values"]["revenue"].update(aliases=["MONTH"]),
    lambda s: s["cases"][0]["expected_rows"].append(s["cases"][0]["expected_rows"][0]),
    lambda s: s["cases"][0]["expected_rows"][0].update(revenue=float("inf")),
    lambda s: s["cases"][0]["expected_rows"][0].update(revenue=None),
    lambda s: s["cases"][0]["result"].update(exact_schema="true"),
    lambda s: s["cases"][0].update(expected_layer="best"),
    lambda s: s["cases"][0].update(reference={"kind": "sql", "sql": "DELETE FROM zebra"}),
    lambda s: s["cases"][0].update(reference={"kind": "sql", "path": "missing.sql"}),
    lambda s: s["cases"][0]["result"].update(ordered=True),
    lambda s: s["cases"][0]["metric"].update(grain=[]),
    lambda s: s["approval"].update(status="requires_owner_review"),
    lambda s: s["approval"].update(reviewer=""),
])
def test_reject_malformed_suite(package, mutation):
    _, _, path, suite = package
    mutation(suite)
    path.write_text(json.dumps(suite))
    with pytest.raises(ContractError):
        load_suite(path)


def test_duplicate_json_key(package):
    *_, path, _ = package
    path.write_text('{"version":1,"version":1}')
    with pytest.raises(ContractError, match="Duplicate JSON key"):
        load_suite(path)


@pytest.mark.parametrize("target", ["database", "context", "suite", "expected"])
def test_provenance_and_freshness_drift(package, target):
    db, context, path, suite = package
    if target == "database":
        conn = duckdb.connect(str(db))
        conn.execute("UPDATE zebra SET amount=99 WHERE month=1")
        conn.close()
    elif target == "context":
        (context / "metric_definitions.md").write_text("Changed definition")
    elif target == "suite":
        suite["cases"][0]["reference"]["sql"] = "SELECT month, 0 AS revenue FROM zebra"
        path.write_text(json.dumps(suite))
    else:
        suite["cases"][0]["expected_rows"][0]["revenue"] = 999
        path.write_text(json.dumps(suite))
    with pytest.raises(ContractError, match="provenance drift"):
        inputs(db, context, path)
    checks = custom_golden.check(db, context, path)
    assert checks[0]["id"] == "__provenance__" and not checks[0]["passed"]
    if target != "context":
        assert not checks[1]["passed"] and checks[1]["id"] == "monthly"


@pytest.mark.parametrize("incorrect,valid,layer,expected", [
    (False, True, "aggregated", True), (True, True, "aggregated", False),
    (False, False, "aggregated", False), (False, True, "fact", False),
    (False, True, None, True),
])
def test_comparator_semantics(package, incorrect, valid, layer, expected):
    _, _, _, suite = package
    case = suite["cases"][0]
    if layer is None:
        case.pop("expected_layer")
    else:
        case["expected_layer"] = layer
    state = {"data": pd.DataFrame({"PERIOD": ["02", "01"], "SALES": [20.0, 99 if incorrect else 12.35], "extra": [1, 2]}),
             "success": True, "valid": valid, "out_of_range": False, "sql": "SELECT * FROM zebra"}
    result = run_custom_eval.score(state, case, {"zebra": "Aggregated"})
    assert result["passed"] == expected
    assert result["result_match"] == (not incorrect)
    assert result["terminal_verified"] == valid
    case["result"]["exact_schema"] = True
    assert not run_custom_eval.score(state, case, {"zebra": "Aggregated"})["result_match"]


def test_production_graph_report_and_logging(package, tmp_path, monkeypatch):
    db, context, path, _ = package
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    client = ScriptedModel([("generate", "SELECT month, amount AS revenue FROM zebra"),
                            ("validate", "VALID: yes"), ("explain", "answer")], [])
    report_path, store_path = tmp_path / "report.json", tmp_path / "eval.db"
    report = run_custom_eval.run(db, context, path, report_path, eval_db=store_path, openai_client=client)
    assert report == json.loads(report_path.read_text())
    assert report["summary"]["passes"] == 1 and report["summary"]["model"] == "test-model"
    case = report["cases"][0]
    assert case["attempt"] == 1 and len(case["attempt_trace"]) == 1
    assert case["total_input_tokens"] == 80 and case["total_output_tokens"] == 8
    assert "expected_rows" not in case and "data" not in case
    conn = setup_eval_db(store_path)
    try:
        row = conn.execute("SELECT run_type, model, attempt_trace, layer_used, run_id FROM query_log").fetchone()
        assert row[:2] == ("custom_eval", "test-model")
        assert json.loads(row[2]) == case["attempt_trace"]
        assert row[3:] == ("aggregated", case["run_id"])
    finally:
        conn.close()
    with pytest.raises(FileExistsError):
        run_custom_eval.run(db, context, path, report_path, eval_db=store_path, openai_client=client)


def test_candidate_never_overwrites_or_approves(package, tmp_path):
    db, context, path, _ = package
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        custom_golden.candidate(db, context, path, path)
    assert path.read_bytes() == before
    output = tmp_path / "candidate.json"
    custom_golden.candidate(db, context, path, output)
    with pytest.raises(ContractError, match="Owner review required"):
        inputs(db, context, output)


def test_manual_reference_remains_explicit(package, tmp_path):
    db, context, path, suite = package
    suite["cases"][0]["reference"] = {"kind": "manual", "note": "Independent worksheet checked by owner"}
    path.write_text(json.dumps(suite))
    output = tmp_path / "manual.json"
    candidate = custom_golden.candidate(db, context, path, output)
    candidate["approval"] = suite["approval"]
    output.write_text(json.dumps(candidate))
    assert inputs(db, context, output)
    assert not custom_golden.check(db, context, output)[0]["passed"]


def test_cli(package, tmp_path, monkeypatch):
    db, context, path, _ = package
    args = ["--db", str(db), "--context-dir", str(context), "--suite", str(path)]
    assert custom_golden.main(["--check", *args]) == 0
    with pytest.raises(SystemExit) as error:
        run_custom_eval.main([])
    assert error.value.code == 2
    with pytest.raises(SystemExit):
        custom_golden.main(["--candidate", *args])
    captured = {}
    def fake_run(db, context, suite, report):
        captured.update(db=db, context=context, suite=suite, report=report)
        return {"summary": {"failures": 0}}
    monkeypatch.setattr(run_custom_eval, "run", fake_run)
    output = tmp_path / "output.json"
    assert run_custom_eval.main([*args, "--report", str(output)]) == 0
    assert captured == {"db": db, "context": context, "suite": path, "report": output}


def test_invalid_inputs_fail_before_graph_or_report(package, tmp_path, monkeypatch):
    db, context, path, suite = package
    suite["approval"]["status"] = "requires_owner_review"
    path.write_text(json.dumps(suite))
    monkeypatch.setattr(run_custom_eval, "build_graph", lambda *a, **kw: pytest.fail("Graph must not be built"))
    output = tmp_path / "report.json"
    with pytest.raises(ContractError, match="Owner review"):
        run_custom_eval.run(db, context, path, output)
    assert not output.exists()


def test_report_failure_categories(package, tmp_path, monkeypatch):
    db, context, path, suite = package
    template = copy.deepcopy(suite)
    template["cases"] = []
    for index in range(4):
        case = copy.deepcopy(suite["cases"][0])
        case.update(id=str(index), question=f"Synthetic question {index}")
        template["cases"].append(case)
    path.write_text(json.dumps(template))
    new_path = tmp_path / "four.json"
    four = custom_golden.candidate(db, context, path, new_path)
    four["approval"] = suite["approval"]
    new_path.write_text(json.dumps(four))
    base = {"data": pd.DataFrame(suite["cases"][0]["expected_rows"]), "success": True, "valid": True,
            "out_of_range": False, "sql": "SELECT * FROM zebra", "attempt": 1, "model": "fake",
            "attempt_trace": [], "total_input_tokens": 10, "total_output_tokens": 1, "cost_usd": 0.1}
    states = iter([base, {**base, "valid": False}, {**base, "data": pd.DataFrame()}, {**base, "sql": "SELECT 1"}])
    monkeypatch.setattr(run_custom_eval, "run_question", lambda *a, **kw: (next(states), "test-id"))
    report = run_custom_eval.run(db, context, new_path, tmp_path / "summary.json", eval_db=":memory:")
    assert report["summary"] == {"total": 4, "passes": 1, "failures": 3, "pass_rate": 0.25, "model": "fake",
                                 "result_correct_but_not_verified": 1, "incorrect_result": 1, "layer_only_failure": 1}


def test_service_error_leaves_explicit_incomplete_report(package, tmp_path, monkeypatch):
    db, context, path, _ = package
    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic service error")
    monkeypatch.setattr(run_custom_eval, "run_question", fail)
    report_path, store_path = tmp_path / "failed.json", tmp_path / "eval.db"
    with pytest.raises(RuntimeError, match="Synthetic service error"):
        run_custom_eval.run(db, context, path, report_path, eval_db=store_path)
    report = json.loads(report_path.read_text())
    assert report["status"] == "error" and report["cases"] == [] and "summary" not in report
    # Production connection was released, so reopening read-write is possible.
    duckdb.connect(str(db)).close()
