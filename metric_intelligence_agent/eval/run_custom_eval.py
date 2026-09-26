"""Run an approved local custom suite through the unchanged production graph."""
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from connectors.duckdb import DuckDBConnector
from eval.custom_suite import digest, file_digest, inputs, parser
from eval.logger import setup_eval_db
from eval.runner import run_question
from eval.scoring import compare_result, _flag
from graph import build_graph
from layer_reporting import detect_layer_used


def score(state, case, layers):
    matched, reasons = compare_result(state.get("data"), case["result"], case["expected_rows"])
    verified = _flag(state, "success", True) and _flag(state, "valid", True) and _flag(state, "out_of_range", False)
    layer = detect_layer_used(state.get("sql"), layers)
    layer_match = None if "expected_layer" not in case else layer == case["expected_layer"]
    if not verified:
        reasons.append("Terminal state is not explicitly verified and in range")
    if layer_match is False:
        reasons.append(f"Layer mismatch: expected {case['expected_layer']}, detected {layer}")
    return {"result_match": matched, "terminal_verified": verified, "layer_match": layer_match,
            "passed": bool(matched and verified and layer_match is not False), "reasons": reasons}


def run(db, context_dir, suite_path, report_path, *, eval_db=None, openai_client=None):
    suite, context, layers, provenance = inputs(db, context_dir, suite_path)
    # Reserve the report before incurring model costs; never overwrite an input.
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("x", encoding="utf-8") as stream:
        stream.write('{"status": "in_progress"}\n')
    report = {"version": 1, "status": "in_progress", "started_at": datetime.now(timezone.utc).isoformat(),
              "provenance": provenance, "suite_sha256": file_digest(suite_path), "cases": []}
    conn = store = None
    try:
        conn = DuckDBConnector().connect({"path": db})
        runtime = build_graph(conn, context, openai_client=openai_client, table_layers=layers)
        store = setup_eval_db(eval_db)
        for case in suite["cases"]:
            state, run_id = run_question(runtime, store, case["question"], run_type="custom_eval", verbose=False)
            outcome = {"id": case["id"], "question": case["question"], **score(state, case, layers),
                       "run_id": run_id, "fixture_id": case["id"], "fixture_sha256": digest(case["expected_rows"])}
            for key in ("attempt", "model", "sql", "success", "valid", "out_of_range", "layer_used", "error",
                        "validation_reason", "attempt_trace", "non_attempt_usage", "total_input_tokens", "total_output_tokens", "cost_usd"):
                outcome[key] = state.get(key)
            report["cases"].append(outcome)
            print(f"{'PASS' if outcome['passed'] else 'FAIL'} {case['id']}: result={outcome['result_match']} verified={outcome['terminal_verified']} layer={outcome['layer_match']} attempts={state['attempt']} model={state['model']}")
            if outcome["reasons"]:
                print("  " + "; ".join(outcome["reasons"]))
        cases = report["cases"]
        passed = sum(c["passed"] for c in cases)
        report["summary"] = {"total": len(cases), "passes": passed, "failures": len(cases) - passed,
                             "pass_rate": passed / len(cases), "model": cases[0]["model"],
                             "result_correct_but_not_verified": sum(c["result_match"] and not c["terminal_verified"] for c in cases),
                             "incorrect_result": sum(not c["result_match"] for c in cases),
                             "layer_only_failure": sum(c["result_match"] and c["terminal_verified"] and c["layer_match"] is False for c in cases)}
        report["status"] = "complete"
        print(f"Custom suite: {passed}/{len(cases)} passed")
    except Exception as exc:
        report["status"] = "error"
        report["error"] = str(exc)
        raise
    finally:
        if store is not None:
            store.close()
        if conn is not None:
            conn.close()
        report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    cli = parser(__doc__)
    cli.add_argument("--report", type=Path, default=Path("local_eval/reports") / f"{uuid4()}.json")
    args = cli.parse_args(argv)
    try:
        report = run(args.db, args.context_dir, args.suite, args.report)
        print(f"Report: {args.report}")
        return 0 if report["summary"]["failures"] == 0 else 1
    except (ValueError, OSError, ConnectionError) as exc:
        cli.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
