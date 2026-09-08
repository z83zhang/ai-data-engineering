import hashlib
import os
from pathlib import Path

import streamlit as st

from agent import get_date_range, get_openai_client, load_context
from graph import build_graph
from source_management import get_demo_database, initialize_session


EXAMPLE_QUESTIONS = [
    ("Revenue by region", "What is total revenue by customer region?"),
    ("Daily revenue Jan 1995", "What was daily revenue in January 1995?"),
    (
        "Avg order value 1995",
        "What is average order value by month in 1995?",
    ),
    ("Revenue last year", "What was revenue last year?"),
]


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


def is_hosted_mode():
    """Return whether the deliberately configured hosted-demo mode is active."""
    configured_mode = os.environ.get("APP_DEPLOYMENT_MODE")
    if configured_mode is None:
        try:
            configured_mode = st.secrets.get("APP_DEPLOYMENT_MODE", "local")
        except FileNotFoundError:
            configured_mode = "local"
    return str(configured_mode).casefold() == "hosted"


st.set_page_config(page_title="Metric Intelligence Agent", layout="wide")
initialize_session_state()
hosted_mode = is_hosted_mode()

with st.sidebar:
    st.title("Metric Intelligence Agent")
    if hosted_mode:
        api_key = st.text_input(
            "OpenAI API key",
            type="password",
            key="hosted_openai_api_key",
            help="Used only for this browser session and never saved by the app.",
        ).strip()
        st.caption(
            "Bring your own key. It remains in this session's memory and your "
            "OpenAI account is charged for model usage."
        )
    else:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()

if not hosted_mode and not api_key:
    st.error(
        "OPENAI_API_KEY environment variable is not set. "
        "Set it in the terminal before starting Streamlit."
    )
    st.stop()

openai_client = get_openai_client(api_key) if api_key else None
api_key_fingerprint = (
    hashlib.sha256(api_key.encode("utf-8")).hexdigest() if api_key else None
)
custom_context_dir = Path(__file__).parent / "custom_context"
required_context_files = (
    "table_catalog.md",
    "metric_definitions.md",
    "schema.sql",
)
custom_context_available = custom_context_dir.is_dir() and all(
    (custom_context_dir / filename).is_file()
    for filename in required_context_files
)
if "conn" not in st.session_state:
    conn = get_demo_database()
    min_date, max_date = get_date_range(conn)
    context = load_context(min_date, max_date)
    initialize_session(
        conn,
        context,
        min_date,
        max_date,
        openai_client=openai_client,
        eval_db_path=":memory:" if hosted_mode else None,
    )
    st.session_state._graph_api_key_fingerprint = api_key_fingerprint
elif st.session_state.get("_graph_api_key_fingerprint") != api_key_fingerprint:
    st.session_state.graph = build_graph(
        st.session_state.conn,
        st.session_state.context,
        openai_client=openai_client,
    )
    st.session_state._graph_api_key_fingerprint = api_key_fingerprint

with st.sidebar:
    if hosted_mode:
        mode = "💬 Query"
        st.caption("Hosted demo: Query mode with synthetic TPC-H data")
        st.info(
            "Custom-source setup is unavailable in this hosted demo. Clone "
            "and run the project locally to connect your own data."
        )
    else:
        mode = st.radio("Mode", ["💬 Query", "⚙️ Setup"], index=0)
    st.info(f"Active query source: {st.session_state.data_source}")
    setup_source_path = st.session_state.get("setup_source_path")
    if setup_source_path and st.session_state.get("custom_conn") is not None:
        setup_path = Path(setup_source_path)
        try:
            setup_display = setup_path.relative_to(Path(__file__).parent.resolve())
        except ValueError:
            setup_display = Path(setup_path.name)
        setup_is_active = (
            st.session_state.get("conn") is st.session_state.get("custom_conn")
        )
        setup_state = "active query source" if setup_is_active else "not yet activated"
        st.info(f"Setup connected to: {setup_display.as_posix()} ({setup_state})")
    else:
        st.info("Setup connected to: none")
    if st.session_state.data_source == "demo":
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
                "Available dimensions: date, month, customer region, customer "
                "nation, market segment"
            )

        st.subheader("Try asking:")
        for index, (label, question) in enumerate(EXAMPLE_QUESTIONS):
            if st.button(
                label,
                key=f"sidebar-example-{index}",
                use_container_width=True,
                disabled=hosted_mode and not api_key,
            ):
                if not st.session_state.get("example_question"):
                    st.session_state.example_question = question
                    st.rerun()
    else:
        with st.expander("Connected database info"):
            st.write("Custom database connected")
            st.write(
                f"Date range: {st.session_state.min_date} to "
                f"{st.session_state.max_date}"
            )

        st.info("💡 Ask a question about your data in the chat")

    if st.button("🗑️ Clear conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.rating_submitted = False
        st.session_state.last_run_id = None
        st.session_state.last_final_state = None
        st.session_state.example_question = None
        st.rerun()

if mode == "💬 Query":
    from app_pages.query import show

    show(api_key_ready=bool(api_key), hosted_mode=hosted_mode)
else:
    from app_pages.setup import show

    show()
