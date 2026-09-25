"""
Run the Metric Intelligence Agent evaluation suite.

This script executes every case in eval.test_suite against the live LangGraph
pipeline, compares complete frozen results, terminal verification, physical
source layers, and out-of-range behavior, and prints diagnostic failures.

Run from metric_intelligence_agent/:
    python -m eval.run_eval
"""

import time
from collections import defaultdict

from agent import get_date_range, load_context, setup_database
from eval.logger import setup_eval_db
from eval.scoring import load_golden, score_result, validate_contract
from eval.runner import run_question
from eval.test_suite import TEST_CASES
from graph import build_graph


def run_eval():
    """Run all eval cases, log every run, and print a summary report."""
    golden = load_golden()
    for case in TEST_CASES:
        if case["failure_mode"] != "out_of_range":
            validate_contract(case, golden)
    conn = setup_database()
    min_date, max_date = get_date_range(conn)
    context = load_context(min_date, max_date)
    graph = build_graph(conn, context)
    eval_conn = setup_eval_db()

    scores = []
    final_states = []

    try:
        for index, test_case in enumerate(TEST_CASES, start=1):
            final_state, _ = run_question(
                graph,
                eval_conn,
                test_case["question"],
                run_type="eval",
                verbose=False,
            )
            score = score_result(final_state, test_case, golden)
            scores.append(score)
            final_states.append(final_state)

            status = "PASS" if score["passed"] else "FAIL"
            print(
                f"[{status}] {index:02d}/{len(TEST_CASES)} "
                f"{test_case['failure_mode']}: {test_case['question']} "
                f"[result_match={score['result_match']}, "
                f"terminal_verified={score['terminal_verified']}, "
                f"layer_match={score['layer_match']}, "
                f"attempt={final_state['attempt']}]"
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
                failed_checks = score["reasons"]
                print(
                    f"- {score['question']} "
                    f"failed: {', '.join(failed_checks)}"
                )
        else:
            print("\nFailed questions: none")
    finally:
        eval_conn.close()
        conn.close()


if __name__ == "__main__":
    run_eval()
