import time
import uuid

from eval.logger import log_run


def run_question(graph, eval_conn, question, run_type="adhoc", verbose=True):
    """
    Run one question through the graph, log it to eval.db, and return final state.

    Args:
        graph: Compiled LangGraph graph from build_graph().
        eval_conn: Active eval.db connection from setup_eval_db().
        question: Plain English analytics question.
        run_type: Either "adhoc" for manual runs or "eval" for suite runs.
        verbose: Whether to print question, reflection, answer, and cost output.

    Returns:
        Final LangGraph state dict produced by graph.stream().
    """
    if verbose:
        print("\nQuestion:", question)
        print("-" * 50)
    initial_state = {
        "question": question,
        "sql": "",
        "out_of_range": False,
        "attempt": 1,
        "success": False,
        "data": None,
        "error": "",
        "valid": False,
        "validation_reason": "",
        "explanation": "",
        "final_answer": "",
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "cost_usd": 0.0,
    }

    final_state = None
    # thread_id generated for future checkpointer integration
    _thread_id = str(uuid.uuid4())
    start_time = time.perf_counter()
    # stream_mode="values" yields full state after each node.
    for state_update in graph.stream(initial_state, stream_mode="values"):
        final_state = state_update
        if verbose and state_update["attempt"] > 1:
            print(f"  → reflection attempt {state_update['attempt']}")
    response_time_ms = int((time.perf_counter() - start_time) * 1000)

    if final_state is None:
        raise RuntimeError("Graph produced no output")

    log_run(eval_conn, final_state, response_time_ms, run_type)

    if verbose:
        print(final_state["final_answer"])
        cost = final_state["cost_usd"]
        print(
            f"\n📊 Tokens: {final_state['total_input_tokens']} in / "
            f"{final_state['total_output_tokens']} out | "
            f"Est. cost: ${cost:.4f}"
        )

    return final_state
