import os

import streamlit as st

if not os.environ.get("OPENAI_API_KEY"):
    st.error(
        "⚠️ OPENAI_API_KEY environment variable is not set. "
        "Please set it before running the app."
    )
    st.stop()

from agent import get_date_range, load_context, setup_database
from eval.logger import setup_eval_db
from graph import build_graph


EXAMPLE_QUESTIONS = [
    ("Revenue by region", "What is total revenue by customer region?"),
    ("Daily revenue Jan 1995", "What was daily revenue in January 1995?"),
    (
        "Avg order value 1995",
        "What is average order value by month in 1995?",
    ),
    ("Revenue last year", "What was revenue last year?"),
]


@st.cache_resource
def get_demo_database():
    return setup_database()


def get_demo_graph(_conn, context):
    return build_graph(_conn, context)


def initialize_session(conn, context, min_date, max_date):
    """Assign database-dependent resources and metadata for the active source."""
    st.session_state.conn = conn
    st.session_state.context = context
    st.session_state.graph = get_demo_graph(conn, context)
    st.session_state.eval_conn = setup_eval_db()
    st.session_state.data_source = "demo"
    st.session_state.min_date = min_date
    st.session_state.max_date = max_date


def initialize_session_state():
    """Initialize source-independent UI state without overwriting existing values."""
    # TODO: replace full final_state storage with
    # serializable display dict to reduce memory usage
    # as chat history grows
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("rating_submitted", False)
    st.session_state.setdefault("last_run_id", None)
    st.session_state.setdefault("last_final_state", None)
    st.session_state.setdefault("example_question", None)
    # TODO: Replace with Redis/database cache for production multi-user deployments.
    # TODO: Implement semantic caching using embeddings.
    st.session_state.setdefault("query_cache", {})


st.set_page_config(page_title="Metric Intelligence Agent", layout="wide")
initialize_session_state()
if "conn" not in st.session_state:
    conn = get_demo_database()
    min_date, max_date = get_date_range(conn)
    context = load_context(min_date, max_date)
    initialize_session(conn, context, min_date, max_date)

with st.sidebar:
    st.title("Metric Intelligence Agent")
    mode = st.radio("Mode", ["💬 Query", "⚙️ Setup"], index=0)
    st.info(f"Current data source: {st.session_state.data_source}")
    with st.expander("Demo dataset info"):
        st.write("Dataset: TPC-H (scale factor 0.1)")
        st.write(
            f"Date range: {st.session_state.min_date} to "
            f"{st.session_state.max_date}"
        )
        st.write(
            "Available metrics: revenue, order volume, average order value, "
            "discount rate"
        )
        st.write(
            "Available dimensions: date, month, customer region, customer nation, "
            "market segment"
        )

    st.subheader("Try asking:")
    for index, (label, question) in enumerate(EXAMPLE_QUESTIONS):
        if st.button(label, key=f"sidebar-example-{index}", use_container_width=True):
            if not st.session_state.get("example_question"):
                st.session_state.example_question = question
                st.rerun()

    if st.button("🗑️ Clear conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.rating_submitted = False
        st.session_state.last_run_id = None
        st.session_state.last_final_state = None
        st.session_state.example_question = None
        st.rerun()

if mode == "💬 Query":
    from app_pages.query import show

    show()
else:
    from app_pages.setup import show

    show()
