# Metric Intelligence Agent

## Project Purpose

A generalized agentic tool that connects to any database schema, ingests company-specific metric definitions, and returns verified SQL answers to non-technical users with a two-layer reflection check. Demo dataset: TPC-H. Target users: analytics engineers (setup), PMs and data scientists (query).

## Stack and Environment

- Python project using `openai`, `duckdb`, and `pandas`.
- OpenAI chat completions use `gpt-4o`.
- DuckDB runs in memory with the TPC-H extension loaded at runtime.
- Required environment variable:
  - `OPENAI_API_KEY`: used to initialize the OpenAI client. The app raises `ValueError` at import time if it is missing.
- Optional environment variable:
  - `MAX_REFLECTION_ATTEMPTS`: configures reflection retry count.
- `MAX_REFLECTION_ATTEMPTS` defaults to `3`, configurable via environment variable.
- All LLM calls use `temperature=0` for determinism.

## Project Structure

- `agent.py`: Core database setup, context loading, SQL generation, execution, reflection, validation, and explanation functions.
- `main.py`: Demo pipeline runner that wires the agent functions together and runs example questions.
- `requirements.txt`: Python dependencies for OpenAI, DuckDB, and pandas.
- `context/table_catalog.md`: Warehouse layer guidance, table descriptions, joins, and caveats.
- `context/metric_definitions.md`: Canonical metric formulas, source tables, and ambiguity rules.
- `context/schema.sql`: Base TPC-H schema definitions.
- `.gitignore`: Local ignore rules.

## Function Reference

### `agent.py`

`load_context(min_date, max_date) -> str`

- `min_date`: earliest available order date as a `YYYY-MM-DD` string.
- `max_date`: latest available order date as a `YYYY-MM-DD` string.
- Returns a single structured context string containing table catalog, metric definitions, schema, and data range guidance.
- Raises `FileNotFoundError` if any required context file is missing.
- No LLM calls inside `load_context()` ever.

`setup_database() -> duckdb.DuckDBPyConnection`

- Creates an in-memory DuckDB database.
- Installs and loads the TPC-H extension.
- Generates TPC-H data at scale factor `0.1`.
- Builds runtime aggregate tables `agg_daily_sales` and `agg_monthly_sales`.
- Returns an active DuckDB connection.
- No LLM calls inside `setup_database()` ever.

`get_date_range(conn) -> tuple[str, str]`

- `conn`: active DuckDB connection.
- Queries `orders` for minimum and maximum `o_orderdate`.
- Returns `(min_date, max_date)` as ISO date strings.
- No LLM calls inside `get_date_range()` ever.

`run_sql(conn, sql) -> dict`

- `conn`: active DuckDB connection.
- `sql`: SQL string to execute.
- Returns `{"success": True, "data": DataFrame, "sql": sql}` on success.
- Returns `{"success": False, "error": error_message, "sql": sql}` on failure.
- No LLM calls inside `run_sql()` ever.

`reflect_sql(conn, context, question, sql, error, attempt) -> dict`

- `conn`: active DuckDB connection.
- `context`: structured context string from `load_context()`.
- `question`: original plain English user question.
- `sql`: SQL from the previous failed or semantically wrong attempt.
- `error`: DuckDB error message or semantic failure description from `validate_result()`.
- `attempt`: current attempt number, starting at `1` after a failed attempt.
- Returns a `run_sql()` result dict with an added `"attempts"` key, or a max-attempts failure dict.
- The `error` argument accepts both DuckDB error messages (technical failure) and semantic failure descriptions from `validate_result()`. It is not limited to DuckDB errors.

`validate_result(question, sql, df, context) -> dict`

- `question`: original plain English user question.
- `sql`: SQL that produced the result.
- `df`: pandas DataFrame containing the query result.
- `context`: structured context string from `load_context()`.
- Returns `{"valid": True, "reason": None}` when valid.
- Returns `{"valid": False, "reason": reason}` when invalid.
- Runs Python checks first: zero rows, all-null numeric columns, and negative value warnings.
- Runs one LLM semantic check only if Python checks pass.

`generate_sql(context, question) -> str`

- `context`: structured context string returned by `load_context()`.
- `question`: plain English analytics question from the user.
- Returns a raw DuckDB SQL string with no markdown formatting or explanation.
- If the question refers to dates outside the data range, returns a string starting with `OUT_OF_RANGE:` instead of SQL. This is a contract with `run_pipeline()`, which must check for this prefix before passing output to `run_sql()`.

`explain_result(question, sql, df, context) -> str`

- `question`: original plain English user question.
- `sql`: SQL that produced the verified result.
- `df`: pandas DataFrame containing the verified result.
- `context`: structured context string from `load_context()`.
- Returns a plain English explanation string under 150 words for non-technical users.

### `main.py`

`print_failure(error, sql) -> None`

- `error`: technical or semantic failure reason.
- `sql`: final SQL attempted.
- Prints a consistent failure message, including the reason and SQL attempted.
- Purpose: centralizes failure output formatting for exhausted technical retries or semantic validation failures.

`run_pipeline(question, conn, context) -> None`

- `question`: plain English analytics question.
- `conn`: active DuckDB connection.
- `context`: structured context string from `load_context()`.
- Generates SQL, handles out-of-range responses, executes SQL, reflects on technical failures, validates semantic correctness, reflects once on semantic failure, explains verified results, and prints output.

## Pipeline Flow

1. `setup_database()` creates the in-memory TPC-H database and runtime aggregate tables.
2. `get_date_range(conn)` discovers the available order date range.
3. `load_context(min_date, max_date)` combines company context files with data range rules.
4. `run_pipeline(question, conn, context)` asks `generate_sql()` to produce SQL or an `OUT_OF_RANGE:` message.
5. If SQL starts with `OUT_OF_RANGE:`, the pipeline prints the message and stops before calling `run_sql()`.
6. Otherwise, `run_sql()` executes the SQL.
7. Technical failures are sent to `reflect_sql()` until success or `MAX_ATTEMPTS`.
8. Successful results are checked by `validate_result()`.
9. If validation fails, the semantic failure reason is sent to `reflect_sql()` and validated again.
10. Verified results are passed to `explain_result()` and printed with the result table.

## Conventions

- No LLM calls inside `load_context()`, `setup_database()`, `get_date_range()`, or `run_sql()` ever.
- Context files are the only thing that changes between company deployments; `agent.py` never hardcodes business rules.
- Never change function signatures without updating all callers: `load_context`, `run_sql`, `reflect_sql`, `validate_result`, and `explain_result`.
- Always run `python main.py` to verify changes work.

## Known Limitations

- `agg_daily_sales` and `agg_monthly_sales` are virtual tables built at runtime; they are not in `schema.sql`.
- Relative time references such as "last year" and "recent" are intentionally flagged as out of range. This is a design decision, not a bug.
- Semantic validation relies on LLM judgment, which may occasionally pass incorrect results.
