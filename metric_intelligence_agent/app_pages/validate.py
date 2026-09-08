"""Custom-source setup validation UI."""

import uuid

import streamlit as st

from setup_validation import (
    MAX_VALIDATION_QUESTIONS,
    ROW_COUNT_COLUMN,
    deterministic_suggestion,
    load_validation,
    save_validation,
    source_key,
    validation_ready,
)
from source_management import switch_data_source


def _editor(context_dir, source_path, document, prefix):
    questions = document.get("questions", [])
    count = st.number_input(
        "Number of validation questions", 1, MAX_VALIDATION_QUESTIONS,
        max(1, len(questions)), key=f"{prefix}_count"
    )
    edited = []
    for index in range(int(count)):
        saved = questions[index] if index < len(questions) else {}
        st.markdown(f"**Question {index + 1}**")
        text = st.text_input(
            "Question", value=saved.get("question", ""),
            key=f"{prefix}_question_{index}"
        )
        left, right = st.columns(2)
        with left:
            column = st.text_input(
                "Expected column (or __row_count__)",
                value=saved.get("expected_column", ""),
                key=f"{prefix}_column_{index}",
                help=f"Use {ROW_COUNT_COLUMN} to check the returned row count.",
            )
        with right:
            value = st.text_input(
                "Expected number (optional)",
                value=saved.get("expected_value", ""),
                key=f"{prefix}_value_{index}",
            )
        edited.append({
            "id": saved.get("id", str(uuid.uuid4())),
            "question": text.strip(),
            "expected_column": column.strip(),
            "expected_value": value.strip(),
        })
    invalid_expected = any(
        item["expected_value"] and not item["expected_column"] for item in edited
    )
    if invalid_expected:
        st.warning("Each expected number needs a named result column.")
    if st.button(
        "Save validation questions",
        key=f"{prefix}_save_questions",
        disabled=(st.session_state.get("validation_in_flight", False)
                  or invalid_expected or any(not item["question"] for item in edited)),
    ):
        document.update(questions=edited, results={}, ready=False, stale=False)
        save_validation(context_dir, source_path, document)
        st.session_state.setdefault("validation_run_details", {}).pop(prefix, None)
        st.success("Validation questions saved; the prior set was backed up.")
        st.rerun()


def _run_batch(
    context_dir,
    source_path,
    document,
    question_ids,
    prefix,
    executor=None,
    status_events=None,
):
    from eval.logger import detect_layer_used

    if executor is None:
        from agent import load_context
        from eval.runner import run_question
        from graph import build_graph

        conn = st.session_state.get("custom_conn")
        if conn is None:
            st.error("Reconnect the custom source before running validation.")
            return
        graph = build_graph(conn, load_context(context_dir=context_dir))

        def executor(question):
            state, _ = run_question(
                graph, None, question, verbose=False, log=False
            )
            return state

    details = st.session_state.setdefault("validation_run_details", {}).setdefault(
        prefix, {}
    )
    results = document.setdefault("results", {})
    selected = [q for q in document["questions"] if q["id"] in question_ids]
    events = status_events if status_events is not None else []
    for question in selected:
        events.append({"question_id": question["id"], "status": "queued"})
    statuses = {
        q["id"]: st.status(f"Queued: {q['question']}", state="running")
        for q in selected
    }
    for question in selected:
        status = statuses[question["id"]]
        events.append({"question_id": question["id"], "status": "running"})
        status.update(label=f"Running: {question['question']}", state="running")
        try:
            state = executor(question["question"])
            system_success = bool(
                state.get("success") and state.get("valid")
                and not state.get("out_of_range")
            )
        except Exception as error:
            state = {"sql": "", "data": None, "explanation": "", "error": str(error),
                     "attempt": 0, "success": False, "valid": False,
                     "out_of_range": False}
            system_success = False
        suggestion, reason = None, "No automated check configured."
        if system_success:
            suggestion, reason = deterministic_suggestion(
                state["data"], question.get("expected_column", ""),
                question.get("expected_value", "")
            )
        details[question["id"]] = state
        results[question["id"]] = {
            "system_success": system_success,
            "detected_layer": detect_layer_used(state.get("sql", "")) or "unknown",
            "attempt": state.get("attempt", 0),
            "suggestion": suggestion,
            "suggestion_reason": reason,
            "analyst_decision": None,
            "failure_origin": "system failure" if not system_success else None,
        }
        status.update(
            label=("Awaiting analyst review: " if system_success
                   else "Fail — system failure: ") + question["question"],
            state="complete" if system_success else "error",
        )
        events.append({
            "question_id": question["id"],
            "status": "completed",
            "system_success": system_success,
        })
    document.update(ready=False, stale=False)
    save_validation(context_dir, source_path, document, backup=False)


def _result(context_dir, source_path, document, question, prefix):
    result = document.get("results", {}).get(question["id"])
    if not result:
        st.caption("Queued — not run yet")
        return
    decision = result.get("analyst_decision")
    if not result["system_success"]:
        st.error("Fail — system failure (technical error or exhausted retries)")
    elif decision == "pass":
        st.success("Pass — analyst marked")
    elif decision == "fail":
        st.error("Fail — analyst-marked fail (the system completed successfully)")
    else:
        st.warning("System completed — analyst decision required")
    st.caption(
        f"Layer: {result.get('detected_layer', 'unknown')} | "
        f"Attempts: {result.get('attempt', 0)}"
    )
    suggestion = result.get("suggestion")
    if suggestion is not None:
        kind = "pass" if suggestion else "fail"
        st.info(f"Automated suggestion: {kind}. {result['suggestion_reason']}")
    else:
        st.caption("Automated suggestion: none — analyst judgment only.")

    state = st.session_state.get("validation_run_details", {}).get(prefix, {}).get(
        question["id"]
    )
    if state is None:
        st.info("Full run context is available after re-running in this session.")
    else:
        st.markdown("**SQL**")
        st.code(state.get("sql") or "No SQL produced.", language="sql")
        st.markdown("**Actual result**")
        if state.get("data") is None:
            st.write(state.get("error") or "No result returned.")
        else:
            st.dataframe(state["data"], width="stretch")
        st.markdown("**Explanation**")
        st.write(state.get("explanation") or state.get("error") or "None")

    if result["system_success"]:
        left, right = st.columns(2)
        chosen = None
        with left:
            if st.button("Mark pass", key=f"{prefix}_pass_{question['id']}"):
                chosen = "pass"
        with right:
            if st.button("Mark fail", key=f"{prefix}_fail_{question['id']}"):
                chosen = "fail"
        if chosen:
            result["analyst_decision"] = chosen
            result["failure_origin"] = "analyst-marked fail" if chosen == "fail" else None
            document["ready"] = validation_ready(document)
            save_validation(context_dir, source_path, document, backup=False)
            st.rerun()
    elif st.button("Confirm fail", key=f"{prefix}_system_fail_{question['id']}"):
        result["analyst_decision"] = "fail"
        save_validation(context_dir, source_path, document, backup=False)
        st.rerun()


def show(context_dir, executor=None, status_events=None):
    source_path = st.session_state.get("setup_source_path")
    if not source_path:
        st.warning("Connect the custom source to configure and run its validation set.")
        return
    prefix = source_key(source_path)
    document = load_validation(context_dir, source_path)
    st.caption(
        "Known-answer questions run through the real graph. Numeric checks are "
        "advisory; the analyst's pass/fail decision is final."
    )
    _editor(context_dir, source_path, document, prefix)
    in_flight = st.session_state.get("validation_in_flight", False)
    ids = [q["id"] for q in document["questions"]]
    failed = [q["id"] for q in document["questions"]
              if document.get("results", {}).get(q["id"], {}).get("failure_origin")]
    left, right = st.columns(2)
    with left:
        if (
            st.button(
                "Run all", key=f"{prefix}_run_all", disabled=in_flight or not ids
            )
            and not in_flight
        ):
            st.session_state.validation_pending_ids = ids
            st.session_state.validation_in_flight = True
            st.rerun()
    with right:
        if (
            st.button(
                "Run previously failed",
                key=f"{prefix}_run_failed",
                disabled=in_flight or not failed,
            )
            and not in_flight
        ):
            st.session_state.validation_pending_ids = failed
            st.session_state.validation_in_flight = True
            st.rerun()
    pending = st.session_state.pop("validation_pending_ids", None)
    if in_flight and pending:
        try:
            _run_batch(
                context_dir,
                source_path,
                document,
                pending,
                prefix,
                executor=executor,
                status_events=status_events,
            )
        finally:
            st.session_state.validation_in_flight = False
        st.rerun()

    for index, question in enumerate(document["questions"]):
        with st.expander(f"{index + 1}. {question['question']}", expanded=True):
            if (
                st.button(
                    "Run this question",
                    key=f"{prefix}_run_{question['id']}",
                    disabled=in_flight,
                )
                and not in_flight
            ):
                st.session_state.validation_pending_ids = [question["id"]]
                st.session_state.validation_in_flight = True
                st.rerun()
            _result(context_dir, source_path, document, question, prefix)

    has_failure = any(r.get("failure_origin")
                      for r in document.get("results", {}).values())
    if document.get("ready") and validation_ready(document):
        st.success("Ready for Query mode — all validation questions passed.")
    elif has_failure:
        st.warning("Not ready for Query mode — one or more questions failed.")
    else:
        st.info("Not ready for Query mode — run and review every question.")
    if document.get("stale"):
        st.warning("Readiness is stale because Data Source or Metric Definitions changed.")

    connected = st.session_state.get("custom_conn") is not None
    if st.button("Activate custom source", key=f"{prefix}_activate", type="primary",
                 disabled=not document.get("ready") or not connected or in_flight):
        switch_data_source(st.session_state.custom_conn, context_dir=context_dir,
                           data_source="custom")
        st.rerun()
