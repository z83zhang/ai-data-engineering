"""
Run the Metric Intelligence Agent evaluation suite.

This script executes every case in eval.test_suite against the live LangGraph
pipeline, scores row count, required columns, expected layer, and out-of-range
behavior, logs each run to eval.db, and prints a summary report.

Run from metric_intelligence_agent/:
    python -m eval.run_eval
"""

import time
from collections import defaultdict

from agent import get_date_range, load_context, setup_database
from eval.logger import detect_layer_used, setup_eval_db
from eval.runner import run_question
from eval.test_suite import TEST_CASES
from graph import build_graph


def score_result(final_state, test_case):
    """Score one final graph state against one eval test case."""
    data = final_state["data"]
    expected_row_count = test_case["expected_row_count"]
    expected_columns = test_case["expected_columns"]
    expected_layer = test_case["expected_layer"]

    if expected_row_count is None or data is None:
        row_count_match = None
    else:
        row_count_match = len(data) == expected_row_count

    if expected_columns is None or data is None:
        column_match = None
    else:
        column_match = all(column in data.columns for column in expected_columns)

    expected_value = test_case.get("expected_value")
    expected_value_tolerance = test_case.get("expected_value_tolerance", 0.01)
    expected_value_aggregation = test_case.get("expected_value_aggregation", "sum")

    if expected_value is None or data is None:
        value_match = None
    else:
        numeric_cols = data.select_dtypes(include="number").columns
        grouping_columns = expected_columns or []
        value_candidates = [
            column for column in numeric_cols if column not in grouping_columns
        ]
        value_column = value_candidates[0] if value_candidates else numeric_cols[0]

        if expected_value_aggregation == "first":
            ordered_data = (
                data.sort_values(by=expected_columns) if expected_columns else data
            )
            actual = ordered_data[value_column].iloc[0]
        else:
            actual = data[value_column].sum()

        if expected_value == 0:
            value_match = actual == 0
        else:
            value_match = (
                abs(actual - expected_value) / abs(expected_value)
                <= expected_value_tolerance
            )

    if expected_layer is None or data is None:
        layer_match = None
    else:
        layer_match = detect_layer_used(final_state["sql"]) == expected_layer

    out_of_range_match = True
    if test_case["failure_mode"] == "out_of_range":
        out_of_range_match = final_state["out_of_range"] is True

    checks = [
        row_count_match,
        column_match,
        value_match,
        layer_match,
        out_of_range_match,
    ]
    passed = all(check is not False for check in checks)

    return {
        "passed": passed,
        "row_count_match": row_count_match,
        "column_match": column_match,
        "value_match": value_match,
        "layer_match": layer_match,
        "out_of_range_match": out_of_range_match,
        "failure_mode": test_case["failure_mode"],
        "question": test_case["question"],
        "notes": test_case["notes"],
    }


def run_eval():
    """Run all eval cases, log every run, and print a summary report."""
    conn = setup_database()
    min_date, max_date = get_date_range(conn)
    context = load_context(min_date, max_date)
    graph = build_graph(conn, context)
    eval_conn = setup_eval_db()

    scores = []
    final_states = []

    try:
        for index, test_case in enumerate(TEST_CASES, start=1):
            final_state = run_question(
                graph,
                eval_conn,
                test_case["question"],
                run_type="eval",
                verbose=False,
            )
            score = score_result(final_state, test_case)
            scores.append(score)
            final_states.append(final_state)

            status = "PASS" if score["passed"] else "FAIL"
            print(
                f"[{status}] {index:02d}/{len(TEST_CASES)} "
                f"{test_case['failure_mode']}: {test_case['question']}"
            )
            time.sleep(10)

        total = len(scores)
        passed = sum(1 for score in scores if score["passed"])
        print("\nSummary")
        print("-" * 50)
        print(f"Total pass rate: {passed}/{total} ({passed / total:.1%})")

        by_mode = defaultdict(list)
        for score in scores:
            by_mode[score["failure_mode"]].append(score)

        print("\nPass rate by failure_mode:")
        for failure_mode, mode_scores in sorted(by_mode.items()):
            mode_passed = sum(1 for score in mode_scores if score["passed"])
            mode_total = len(mode_scores)
            print(
                f"- {failure_mode}: {mode_passed}/{mode_total} "
                f"({mode_passed / mode_total:.1%})"
            )

        average_attempts = (
            sum(final_state["attempt"] for final_state in final_states)
            / len(final_states)
        )
        total_cost = sum(fs["cost_usd"] for fs in final_states)
        print(f"\nAverage attempts: {average_attempts:.2f}")
        print(f"Total cost: ${total_cost:.4f}")

        failed_scores = [score for score in scores if not score["passed"]]
        if failed_scores:
            print("\nFailed questions:")
            for score in failed_scores:
                failed_checks = [
                    name
                    for name in (
                        "row_count_match",
                        "column_match",
                        "value_match",
                        "layer_match",
                        "out_of_range_match",
                    )
                    if score[name] is False
                ]
                print(
                    f"- {score['question']} "
                    f"failed: {', '.join(failed_checks)}"
                )
        else:
            print("\nFailed questions: none")
    finally:
        eval_conn.close()


if __name__ == "__main__":
    run_eval()
