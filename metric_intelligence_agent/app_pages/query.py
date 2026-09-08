import re

import streamlit as st
from openai import AuthenticationError

from eval.logger import detect_layer_used, update_human_rating
from eval.runner import run_question


def clean_explanation(text):
    """Remove common Markdown formatting from an explanation."""
    # Remove markdown links [text](url) → text
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    # Remove backtick code spans
    text = re.sub(r"`[^`]*`", "", text)
    # Remove bold/italic markers
    text = re.sub(r"\*\*|__|\*|_", "", text)
    text = text.replace("$", "\\$")
    return text.strip()


def _formatted_data(df):
    """Prepare dates and floats for display without modifying the result data."""
    display_df = df.copy()
    datetime_cols = display_df.select_dtypes(
        include=["datetime", "datetimetz"]
    ).columns
    for col in datetime_cols:
        display_df[col] = display_df[col].dt.strftime("%Y-%m-%d")

    format_cols = {
        col: "{:,.0f}"
        for col in display_df.select_dtypes(include="float").columns
    }
    return display_df.style.format(format_cols)


def _show_feedback(msg, hosted_mode=False):
    run_id = msg["run_id"]
    if run_id is None:
        return

    thumbs_up, thumbs_down = st.columns(2)
    if thumbs_up.button("👍 Correct", key=f"correct-{run_id}"):
        try:
            update_human_rating(st.session_state.eval_conn, run_id, "correct")
            st.success("Thanks for your feedback!")
            msg["rated"] = True
            st.session_state.rating_submitted = True
        except Exception as error:
            if not hosted_mode:
                st.error(f"Could not save rating: {error}")
            else:
                st.error("Could not save the rating. Please try again.")

    if thumbs_down.button("👎 Incorrect", key=f"wrong-{run_id}"):
        try:
            update_human_rating(st.session_state.eval_conn, run_id, "wrong")
            st.success("Thanks for your feedback!")
            msg["rated"] = True
            st.session_state.rating_submitted = True
        except Exception as error:
            if not hosted_mode:
                st.error(f"Could not save rating: {error}")
            else:
                st.error("Could not save the rating. Please try again.")


def _show_result(msg, is_latest, hosted_mode=False):
    final_state = msg["final_state"]
    if final_state["out_of_range"]:
        message = final_state["sql"].replace("OUT_OF_RANGE:", "", 1).strip()
        st.warning(message)
        return

    if final_state["success"] and final_state["valid"]:
        st.success(
            f"✅ Answer verified — {final_state['attempt']} attempt(s)"
        )
        layer = detect_layer_used(final_state["sql"]) or "unknown"
        if msg.get("from_cache"):
            st.caption(f"⚡ Cached | Layer: {layer} | Cost: \\$0.00")
        else:
            st.caption(
                f"Layer: {layer} | "
                f"Attempts: {final_state['attempt']} | "
                f"Cost: \\${final_state['cost_usd']:.4f}"
            )
        df = final_state["data"]
        styled_df = _formatted_data(df)
        st.dataframe(
            styled_df,
            use_container_width=True,
            height=min((len(df) + 1) * 35 + 3, 400),
        )
        explanation = clean_explanation(final_state["explanation"])
        st.write(explanation)

        with st.expander("View generated SQL"):
            st.code(final_state["sql"], language="sql")

        if is_latest and msg["role"] == "assistant" and not msg.get("rated"):
            _show_feedback(msg, hosted_mode=hosted_mode)
        return

    if hosted_mode:
        st.error(
            "The hosted demo could not produce a verified answer. "
            "Try rephrasing the question."
        )
    else:
        st.error(final_state["error"])
    with st.expander("View last SQL attempted"):
        st.code(final_state["sql"], language="sql")


def show(api_key_ready=True, hosted_mode=False):
    st.info(f"Current data source: {st.session_state.data_source}")

    if hosted_mode and not api_key_ready:
        st.warning("Enter your OpenAI API key in the sidebar before asking a question.")

    pending = st.session_state.get("example_question")
    if pending:
        del st.session_state["example_question"]
        question = pending
    else:
        question = st.chat_input(
            "Ask a question about your data",
            disabled=not api_key_ready,
        )

    if question is not None and len(question.strip()) < 5:
        st.warning("Please enter a more specific question.")
        st.stop()
    if question is not None and not any(c.isalpha() for c in question):
        st.warning("Please enter a question in plain English.")
        st.stop()

    if question:
        if not api_key_ready:
            st.warning("Enter your OpenAI API key before asking a question.")
            st.stop()
        history = [
            message["content"]
            for message in st.session_state.messages
            if message["role"] == "user"
        ][-2:]
        cache_key = (
            f"{st.session_state.data_source}::"
            f"{question.lower().strip()}"
        )
        st.session_state.messages.append(
            {
                "role": "user",
                "content": question,
            }
        )
        with st.spinner("Thinking..."):
            if cache_key in st.session_state.query_cache:
                cached = st.session_state.query_cache[cache_key]
                final_state = cached["final_state"]
                run_id = cached["run_id"]
                from_cache = True
            else:
                try:
                    final_state, run_id = run_question(
                        st.session_state.graph,
                        st.session_state.eval_conn,
                        question,
                        conversation_history=history,
                        verbose=False,
                    )
                except AuthenticationError:
                    st.error(
                        "OpenAI rejected this API key. Check that it is valid, "
                        "active, and has available API billing or credits."
                    )
                    st.stop()
                except Exception:
                    if not hosted_mode:
                        raise
                    st.error(
                        "The hosted demo could not complete that request. "
                        "Please try again in a moment."
                    )
                    st.stop()
                st.session_state.query_cache[cache_key] = {
                    "final_state": final_state,
                    "run_id": run_id,
                }
                from_cache = False

        st.session_state.last_final_state = final_state
        st.session_state.last_run_id = run_id
        st.session_state.messages.append(
            {
                "role": "assistant",
                "final_state": final_state,
                "run_id": run_id,
                "rated": False,
                "from_cache": from_cache,
            }
        )
        st.session_state.rating_submitted = False
        st.rerun()

    for i, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.write(msg["content"])
            elif msg["final_state"] is not None:
                is_latest = i == len(st.session_state.messages) - 1
                _show_result(msg, is_latest, hosted_mode=hosted_mode)
