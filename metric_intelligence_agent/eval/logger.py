import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import duckdb


def setup_eval_db():
    """Create or open metric_intelligence_agent/eval.db and ensure query_log exists."""
    db_path = Path(__file__).parent.parent / "eval.db"
    conn = duckdb.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS query_log (
            run_id VARCHAR PRIMARY KEY,
            timestamp TIMESTAMP,
            question TEXT,
            sql_final TEXT,
            attempts INTEGER,
            technical_pass BOOLEAN,
            semantic_pass BOOLEAN,
            out_of_range BOOLEAN,
            layer_used VARCHAR,
            error_messages TEXT,
            response_time_ms INTEGER,
            total_input_tokens INTEGER,
            total_output_tokens INTEGER,
            cost_usd DECIMAL(10, 6),
            run_type VARCHAR,
            human_rating VARCHAR,
            human_notes TEXT
        )
    """)
    return conn


def detect_layer_used(sql):
    """Detect the highest warehouse layer referenced by a SQL string."""
    if not sql:
        return None

    normalized = sql.lower()
    if re.search(r"\bagg_daily_sales\b|\bagg_monthly_sales\b", normalized):
        return "aggregated"
    if re.search(r"\borders\b|\blineitem\b", normalized):
        return "fact"
    if re.search(r"\bcustomer\b|\bsupplier\b|\bnation\b|\bregion\b", normalized):
        return "dimension"

    return "unknown"


def log_run(conn, final_state, response_time_ms, run_type="adhoc"):
    """
    Insert one LangGraph run into query_log without closing the connection.

    Args:
        conn: Active DuckDB connection returned by setup_eval_db().
        final_state: Final LangGraph state dict for one question.
        response_time_ms: End-to-end latency in milliseconds.
        run_type: Either "adhoc" for manual runs or "eval" for evaluation runs.

    Raises:
        ValueError: If run_type is not "adhoc" or "eval".

    The caller owns connection lifetime; this function does not close conn.
    """
    if run_type not in ("adhoc", "eval"):
        raise ValueError(f"run_type must be 'adhoc' or 'eval', got '{run_type}'")

    out_of_range = final_state["out_of_range"]
    if out_of_range:
        sql_final = None
        technical_pass = None
        semantic_pass = None
        layer_used = None
        error_messages = None
    else:
        sql_final = final_state["sql"]
        technical_pass = final_state["success"]
        semantic_pass = final_state["valid"]
        layer_used = detect_layer_used(final_state["sql"])
        error_messages = final_state["error"] or None

    total_input_tokens = final_state["total_input_tokens"]
    total_output_tokens = final_state["total_output_tokens"]
    cost_usd = final_state["cost_usd"]

    conn.execute(
        """
        INSERT INTO query_log (
            run_id,
            timestamp,
            question,
            sql_final,
            attempts,
            technical_pass,
            semantic_pass,
            out_of_range,
            layer_used,
            error_messages,
            response_time_ms,
            total_input_tokens,
            total_output_tokens,
            cost_usd,
            run_type,
            human_rating,
            human_notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            str(uuid4()),
            datetime.now(),
            final_state["question"],
            sql_final,
            final_state["attempt"],
            technical_pass,
            semantic_pass,
            out_of_range,
            layer_used,
            error_messages,
            response_time_ms,
            total_input_tokens,
            total_output_tokens,
            cost_usd,
            run_type,
            None,
            None,
        ],
    )
