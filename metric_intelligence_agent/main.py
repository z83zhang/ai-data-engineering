import uuid

from agent import get_date_range, load_context, setup_database
from graph import build_graph


def run_question(graph, question):
    print("\nQuestion:", question)
    print("-" * 50)
    thread_id = str(uuid.uuid4())
    # thread_id generated for future checkpointer integration
    # not passed to graph.stream() until MemorySaver is re-added
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
    }
    try:
        final_state = None
        # stream_mode="values" yields full state after each node
        # switch to "updates" for node-level deltas when
        # integrating Streamlit progress indicators
        for state_update in graph.stream(
            initial_state,
            stream_mode="values"
        ):
            final_state = state_update
            if state_update["attempt"] > 1:
                print(f"  → reflection attempt {state_update['attempt']}")
        if final_state is not None:
            print(final_state["final_answer"])
            cost = (
                final_state["total_input_tokens"] / 1_000_000 * 2.50
                + final_state["total_output_tokens"] / 1_000_000 * 10.00
            )
            print(
                f"\n📊 Tokens: {final_state['total_input_tokens']} in / "
                f"{final_state['total_output_tokens']} out | "
                f"Est. cost: ${cost:.4f}"
            )
        else:
            print("❌ Graph produced no output")
    except Exception as error:
        print("❌ Graph error:", error)


conn = setup_database()
min_date, max_date = get_date_range(conn)
context = load_context(min_date, max_date)
graph = build_graph(conn, context)

run_question(graph, "What is the total revenue by customer region?")
run_question(graph, "What is the average order value by month in 1995?")
run_question(graph, "What was revenue last year?")
