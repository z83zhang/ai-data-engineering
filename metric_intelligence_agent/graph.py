from typing import Optional, TypedDict

import pandas as pd
from langgraph.graph import END, StateGraph

from agent import (
    MAX_ATTEMPTS,
    explain_result,
    generate_sql,
    reflect_sql,
    run_sql,
    validate_result,
)


class AgentState(TypedDict):
    question: str
    sql: str
    out_of_range: bool
    attempt: int
    success: bool
    data: Optional[pd.DataFrame]
    error: str
    valid: bool
    validation_reason: str
    explanation: str
    final_answer: str
    total_input_tokens: int
    total_output_tokens: int


def build_graph(conn, context):
    def generate_sql_node(state):
        """Generate SQL or stop early for out-of-range questions."""
        sql, input_tokens, output_tokens = generate_sql(context, state["question"])
        total_input_tokens = state["total_input_tokens"] + input_tokens
        total_output_tokens = state["total_output_tokens"] + output_tokens
        if sql.startswith("OUT_OF_RANGE:"):
            message = sql.replace("OUT_OF_RANGE:", "").strip()
            return {
                "sql": sql,
                "out_of_range": True,
                "final_answer": f"⚠️ {message}",
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
            }

        return {
            "sql": sql,
            "out_of_range": False,
            "attempt": 1,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
        }

    def run_sql_node(state):
        """Run the current SQL against DuckDB."""
        result = run_sql(conn, state["sql"])
        return {
            "success": result["success"],
            "data": result["data"],
            "error": result["error"],
            "sql": result["sql"],
        }

    def reflect_sql_node(state):
        """Rewrite failed SQL using the latest error context."""
        result = reflect_sql(
            conn,
            context,
            state["question"],
            state["sql"],
            state["error"],
            state["attempt"],
        )
        return {
            "sql": result["sql"],
            "success": result["success"],
            "data": result["data"],
            "error": result["message"] or result["error"],
            "attempt": result["attempts"],
            "total_input_tokens": (
                state["total_input_tokens"] + result["input_tokens"]
            ),
            "total_output_tokens": (
                state["total_output_tokens"] + result["output_tokens"]
            ),
        }

    def validate_result_node(state):
        """Validate successful query results for technical and semantic fit."""
        validation = validate_result(
            state["question"],
            state["sql"],
            state["data"],
            context,
        )
        reason = validation["reason"]
        result = {
            "valid": validation["valid"],
            "validation_reason": reason,
            "total_input_tokens": (
                state["total_input_tokens"] + validation["input_tokens"]
            ),
            "total_output_tokens": (
                state["total_output_tokens"] + validation["output_tokens"]
            ),
        }
        if not validation["valid"]:
            result["error"] = reason
        return result

    def explain_result_node(state):
        """Explain a verified result in plain English."""
        explanation, input_tokens, output_tokens = explain_result(
            state["question"],
            state["sql"],
            state["data"],
            context,
        )
        return {
            "explanation": explanation,
            "total_input_tokens": state["total_input_tokens"] + input_tokens,
            "total_output_tokens": state["total_output_tokens"] + output_tokens,
        }

    # Note: output_node and failure_node format output for terminal display.
    # When integrating Streamlit, remove these nodes and read state["data"],
    # state["explanation"], and state["error"] directly from final_state in the
    # UI layer instead.
    def output_node(state):
        """Build the final verified answer string."""
        final_answer = (
            f"✅ Answer verified — {state['attempt']} attempt(s)\n\n"
            "Result:\n"
            f"{state['data'].to_string()}\n\n"
            "Explanation:\n"
            f"{state['explanation']}"
        )
        return {"final_answer": final_answer}

    def failure_node(state):
        """Build the final failure answer string."""
        final_answer = (
            "❌ Could not answer this question.\n"
            f"Reason: {state['error']}\n"
            f"SQL attempted: {state['sql']}"
        )
        return {"final_answer": final_answer}

    def route_after_generate(state):
        """Route generated SQL to execution or end for out-of-range questions."""
        if state["out_of_range"]:
            return "out_of_range"
        return "run_sql"

    def route_after_run(state):
        """Route after SQL execution based on success and attempt budget."""
        if state["success"]:
            return "validate"
        if state["attempt"] < MAX_ATTEMPTS:
            return "reflect"
        return "failure"

    def route_after_validate(state):
        """Route after validation based on semantic fit and attempt budget."""
        if state["valid"]:
            return "explain"
        if state["attempt"] < MAX_ATTEMPTS:
            return "reflect"
        return "failure"

    workflow = StateGraph(AgentState)
    workflow.add_node("generate_sql", generate_sql_node)
    workflow.add_node("run_sql", run_sql_node)
    workflow.add_node("reflect", reflect_sql_node)
    workflow.add_node("validate", validate_result_node)
    workflow.add_node("explain", explain_result_node)
    workflow.add_node("output", output_node)
    workflow.add_node("failure", failure_node)

    workflow.set_entry_point("generate_sql")
    workflow.add_conditional_edges(
        "generate_sql",
        route_after_generate,
        {
            "out_of_range": END,
            "run_sql": "run_sql",
        },
    )
    workflow.add_conditional_edges(
        "run_sql",
        route_after_run,
        {
            "reflect": "reflect",
            "failure": "failure",
            "validate": "validate",
        },
    )
    workflow.add_conditional_edges(
        "validate",
        route_after_validate,
        {
            "reflect": "reflect",
            "failure": "failure",
            "explain": "explain",
        },
    )
    workflow.add_edge("reflect", "run_sql")
    workflow.add_edge("explain", "output")
    workflow.add_edge("output", END)
    workflow.add_edge("failure", END)

    return workflow.compile()
