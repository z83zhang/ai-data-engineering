"""Side-effect-free lifecycle helpers for active query data sources."""

import streamlit as st

from agent import load_context, setup_database
from eval.logger import setup_eval_db
from graph import build_graph


@st.cache_resource
def get_demo_database():
    return setup_database()


def initialize_session(
    conn,
    context,
    min_date,
    max_date,
    context_dir=None,
    data_source="demo",
):
    """Assign database-dependent resources and metadata for the active source."""
    if context is None:
        context = load_context(min_date, max_date, context_dir)

    st.session_state.conn = conn
    st.session_state.context = context
    st.session_state.graph = build_graph(conn, context)
    if "eval_conn" not in st.session_state:
        st.session_state.eval_conn = setup_eval_db()
    st.session_state.data_source = data_source
    st.session_state.min_date = min_date
    st.session_state.max_date = max_date


def switch_data_source(
    conn,
    min_date=None,
    max_date=None,
    context_dir=None,
    data_source="custom",
):
    """Switch sources while preserving the active evaluation log connection."""
    if data_source == "custom":
        min_date = None
        max_date = None

    st.session_state.messages = []
    st.session_state.query_cache = {}
    st.session_state.rating_submitted = False
    st.session_state.last_run_id = None
    st.session_state.last_final_state = None
    st.session_state.example_question = None

    initialize_session(
        conn,
        None,
        min_date,
        max_date,
        context_dir=context_dir,
        data_source=data_source,
    )
