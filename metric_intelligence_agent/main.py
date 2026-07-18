import uuid

from agent import get_date_range, load_context, setup_database
from graph import build_graph


def run_question(graph, question):
    print("\nQuestion:", question)
    print("-" * 50)
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
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
    }
    try:
        final_state = None
        # stream_mode="values" yields full state after each node
        # switch to "updates" for node-level deltas when
        # integrating Streamlit progress indicators
        for state_update in graph.stream(
            initial_state,
            config=config,
            stream_mode="values"
        ):
            final_state = state_update
            if state_update["attempt"] > 1:
                print(f"  → reflection attempt {state_update['attempt']}")
        if final_state is not None:
            print(final_state["final_answer"])
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
