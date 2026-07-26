from agent import get_date_range, load_context, setup_database
from eval.logger import setup_eval_db
from eval.runner import run_question
from graph import build_graph


conn = setup_database()
min_date, max_date = get_date_range(conn)
context = load_context(min_date, max_date)
graph = build_graph(conn, context)
eval_conn = setup_eval_db()

try:
    for question in [
        "What is the total revenue by customer region?",
        "What is the average order value by month in 1995?",
        "What was revenue last year?",
    ]:
        try:
            run_question(graph, eval_conn, question)
        except Exception as error:
            print("❌ Graph error:", error)
finally:
    eval_conn.close()
