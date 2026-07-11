import os
import re
from pathlib import Path

import duckdb
from openai import OpenAI


api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    raise ValueError("OPENAI_API_KEY environment variable is not set.")
client = OpenAI(api_key=api_key)
MAX_ATTEMPTS = int(os.environ.get("MAX_REFLECTION_ATTEMPTS", 3))


def load_context(min_date, max_date):
    """
    Read context files from disk and combine them with the data date range.

    Args:
        min_date: Earliest available order date as a YYYY-MM-DD string.
        max_date: Latest available order date as a YYYY-MM-DD string.

    Returns a string in this format:
        === TABLE CATALOG ===
        ...contents...

        === METRIC DEFINITIONS ===
        ...contents...

        === SCHEMA ===
        ...contents...

        === DATA RANGE ===
        This database contains order data from {min_date} to {max_date}.
        ...

    Raises FileNotFoundError if any context file is missing.
    """
    base_dir = Path(__file__).parent
    files = [
        ("TABLE CATALOG", Path("context/table_catalog.md")),
        ("METRIC DEFINITIONS", Path("context/metric_definitions.md")),
        ("SCHEMA", Path("context/schema.sql")),
    ]

    sections = []
    for title, relative_path in files:
        path = base_dir / relative_path
        if not path.exists():
            raise FileNotFoundError(f"Context file not found: {relative_path.as_posix()}")
        sections.append(f"=== {title} ===\n{path.read_text(encoding='utf-8')}")

    sections.append(
        "=== DATA RANGE ===\n"
        f"This database contains order data from {min_date} to {max_date} only.\n\n"
        "If a question references a time period outside this range -- including\n"
        'relative references such as "last year", "recent", "current", '
        '"latest",\n'
        f'or "this quarter" that would resolve to dates after {max_date} or '
        f"before\n{min_date} -- do NOT generate SQL. Instead return this exact "
        "message and\nnothing else:\n\n"
        f"OUT_OF_RANGE: This dataset only contains data from {min_date} to\n"
        f"{max_date}. Your question refers to a time period outside this range.\n"
        f"Please specify a year between {min_date[:4]} and {max_date[:4]}.\n\n"
        "Only generate SQL if the question refers to a date range that falls\n"
        f"within {min_date} to {max_date}."
    )

    return "\n\n".join(sections)


def setup_database():
    """
    Create an in-memory DuckDB database, load TPC-H data at scale
    factor 0.1, and build the agg_daily_sales and agg_monthly_sales
    aggregate tables.

    Returns an active DuckDB connection with all tables ready to query.
    """
    conn = duckdb.connect(database=":memory:")
    conn.execute("INSTALL tpch")
    conn.execute("LOAD tpch")
    conn.execute("CALL dbgen(sf=0.1)")
    conn.execute("""
        CREATE TABLE agg_daily_sales AS
        SELECT
          o_orderdate AS order_date,
          SUM(l_extendedprice * (1 - l_discount)) AS revenue,
          COUNT(DISTINCT o_orderkey) AS order_volume,
          AVG(l_discount) AS discount_rate
        FROM orders
        JOIN lineitem ON o_orderkey = l_orderkey
        WHERE o_orderstatus <> 'C'
        GROUP BY o_orderdate
    """)
    conn.execute("""
        CREATE TABLE agg_monthly_sales AS
        SELECT
          YEAR(o_orderdate) AS order_year,
          MONTH(o_orderdate) AS order_month,
          SUM(l_extendedprice * (1 - l_discount)) AS revenue,
          COUNT(DISTINCT o_orderkey) AS order_volume,
          AVG(l_discount) AS discount_rate
        FROM orders
        JOIN lineitem ON o_orderkey = l_orderkey
        WHERE o_orderstatus <> 'C'
        GROUP BY YEAR(o_orderdate), MONTH(o_orderdate)
    """)
    return conn


def get_date_range(conn):
    """
    Query the actual date range of order data from the database.

    Args:
        conn: Active DuckDB connection.

    Returns:
        Tuple of (min_date, max_date) as ISO date strings
        in YYYY-MM-DD format.
    """
    result = conn.execute("""
        SELECT
          MIN(o_orderdate) AS min_date,
          MAX(o_orderdate) AS max_date
        FROM orders
    """).df()
    min_date = result["min_date"].iloc[0].strftime("%Y-%m-%d")
    max_date = result["max_date"].iloc[0].strftime("%Y-%m-%d")
    return min_date, max_date


def run_sql(conn, sql):
    """
    Execute SQL against a DuckDB connection and return success data or an error.

    Returns {"success": True, "data": DataFrame, "sql": sql} on success,
    otherwise {"success": False, "error": error_message, "sql": sql}.
    """
    try:
        return {"success": True, "data": conn.execute(sql).df(), "sql": sql}
    except Exception as error:
        return {"success": False, "error": str(error), "sql": sql}


def reflect_sql(conn, context, question, sql, error, attempt):
    """
    Rewrite failed SQL using the LLM, run it, and return the execution result.

    Args:
        conn: Active DuckDB connection.
        context: Structured context string from load_context().
        question: Original plain English user question.
        sql: SQL from the previous failed or semantically wrong attempt.
        error: Description of the technical or semantic problem.
        attempt: Current attempt number, starting at 1 after a failed attempt.

    Returns:
        A run_sql() result dict with "attempts", or a max-attempts failure dict.
    """
    if attempt >= MAX_ATTEMPTS:
        return {
            "success": False,
            "error": error,
            "sql": sql,
            "attempts": attempt,
            "message": (
                "Maximum reflection attempts reached. Could not generate valid "
                "SQL for this question."
            ),
        }

    system_message = (
        "You are a data engineering assistant fixing a SQL query for DuckDB.\n\n"
        "Rules you must follow exactly:\n"
        "- Prefer the aggregated layer (agg_daily_sales, agg_monthly_sales) "
        "first. Only fall back to fact tables when needed.\n"
        "- Always exclude cancelled orders (o_orderstatus <> 'C') unless "
        "the user explicitly asks for all orders.\n"
        "- Follow all metric definitions and caveats in the context exactly.\n"
        "- Return only raw SQL, no markdown, no backticks, no explanation.\n\n"
        "Full context:\n"
        f"{context}"
    )
    user_message = (
        "The following SQL failed to return a correct result.\n\n"
        f"Original question: {question}\n\n"
        f"SQL attempted:\n{sql}\n\n"
        f"Problem:\n{error}\n\n"
        "Rewrite the SQL to fix the problem. Return only raw SQL."
    )
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        temperature=0,
    )
    new_sql = response.choices[0].message.content.strip()
    new_sql = re.sub(r"```[a-zA-Z]*", "", new_sql).replace("```", "").strip()
    result = run_sql(conn, new_sql)
    result["attempts"] = attempt + 1
    return result


def validate_result(question, sql, df, context):
    """
    Validate a query result with Python checks, then one LLM semantic check.

    Args:
        question: Original plain English user question.
        sql: SQL that produced the result.
        df: Pandas DataFrame containing the query result.
        context: Structured context string from load_context().

    Returns:
        {"valid": True, "reason": None} when valid, otherwise
        {"valid": False, "reason": reason}.
    """
    if len(df) == 0:
        return {
            "valid": False,
            "reason": (
                "Query returned 0 rows. The SQL may have an overly restrictive "
                "filter or incorrect join condition."
            ),
        }

    numeric_df = df.select_dtypes(include="number")
    for column in numeric_df.columns:
        if numeric_df[column].isnull().all():
            return {
                "valid": False,
                "reason": f"Numeric column '{column}' contains only null values.",
            }

    warnings = []
    for column in numeric_df.columns:
        if (numeric_df[column] < 0).any():
            warnings.append(
                f"Column '{column}' contains negative values. "
                "Verify whether this is valid for the metric being computed."
            )

    system_message = (
        "You are a data quality validator for a metric intelligence agent.\n"
        "Use the metric definitions and table catalog in the context to\n"
        "judge whether the result is correct.\n\n"
        "Full context:\n"
        f"{context}"
    )
    user_message = (
        f"Original question:\n{question}\n\n"
        f"SQL that was run:\n{sql}\n\n"
        f"First 10 rows:\n{df.head(10).to_string()}\n\n"
        f"Total number of rows returned: {len(df)}\n\n"
        "Warnings from automated checks:\n"
        + ("\n".join(warnings) if warnings else "None")
        + "\n\n"
        "Check:\n"
        "1. Does the result actually answer the question asked?\n"
        "2. Do the categorical values look valid and meaningful?\n"
        "3. Do the numeric values look plausible in magnitude given the metric "
        "definitions in the context?\n"
        "4. Does the row count make sense for this type of question?\n"
        "5. Was the correct data layer used based on the SQL?\n\n"
        "Respond with exactly one of these two formats and nothing else:\n\n"
        "If valid:\n"
        "VALID: yes\n\n"
        "If not valid:\n"
        "VALID: no\n"
        "REASON: explanation of what looks wrong"
    )
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        temperature=0,
    )
    content = response.choices[0].message.content.strip()
    if "VALID: yes" in content:
        return {"valid": True, "reason": None}
    if "VALID: no" in content:
        reason = content.split("REASON:", 1)[1].strip() if "REASON:" in content else ""
        return {"valid": False, "reason": reason}
    return {"valid": True, "reason": None}


def generate_sql(context, question):
    """
    Generate a DuckDB SQL query from warehouse context and a user question.

    Args:
        context: Structured context string returned by load_context().
        question: Plain English analytics question from the user.

    Returns:
        A raw SQL string with no markdown formatting or explanation.
    """
    system_message = (
        "You are a data engineering assistant that writes SQL for DuckDB.\n\n"
        "Rules you must follow exactly:\n"
        "- Prefer the aggregated layer (agg_daily_sales, agg_monthly_sales) "
        "first. Only fall back to fact tables (orders, lineitem) when the "
        "aggregated layer cannot answer the question.\n"
        "- Treat every metric definition and caveat in the context as law. "
        "Do not deviate from canonical formulas, join paths, or exclusion "
        "rules.\n"
        "- Always exclude cancelled orders (o_orderstatus <> 'C') unless "
        "the user explicitly asks for all orders.\n"
        "- If the question is ambiguous about customer vs supplier geography, "
        "default to customer region and note the assumption in a SQL comment "
        "at the top of the query.\n"
        "- Return only raw SQL. No markdown, no backticks, no explanation, "
        "no preamble.\n\n"
        "Full context:\n"
        f"{context}"
    )

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": question},
        ],
        temperature=0,
    )

    sql = response.choices[0].message.content.strip()
    return re.sub(r"```[a-zA-Z]*", "", sql).replace("```", "").strip()


def explain_result(question, sql, df, context):
    """
    Explain a verified query result in plain English for a non-technical user.

    Args:
        question: Original plain English user question.
        sql: SQL that produced the verified result.
        df: Pandas DataFrame containing the verified result.
        context: Structured context string from load_context().

    Returns:
        A plain English explanation string under 150 words.
    """
    system_message = (
        "You are a data analytics assistant explaining query results\n"
        "to a non-technical audience such as product managers and\n"
        "data scientists.\n\n"
        "Use the metric definitions and table catalog in the context\n"
        "to give accurate, precise explanations.\n\n"
        "Full context:\n"
        f"{context}"
    )
    user_message = (
        f"Original question:\n{question}\n\n"
        f"SQL that was run:\n{sql}\n\n"
        f"First 20 rows:\n{df.head(20).to_string()}\n\n"
        f"Total number of rows returned: {len(df)}\n\n"
        "Write a response that:\n"
        "1. Directly answers the question in plain English.\n"
        "2. States the key finding or number upfront.\n"
        "3. References the metric definition used, such as formula and layer.\n"
        "4. Surfaces any assumptions made.\n"
        "5. Sounds like a senior analytics engineer explaining to a PM in Slack.\n"
        "6. Stays under 150 words.\n"
        "7. Does not mention SQL, DuckDB, DataFrames, or technical details."
    )
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        temperature=0,
    )
    return response.choices[0].message.content.strip()
