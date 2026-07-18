# Metric Intelligence Agent

## Project Purpose

A generalized agentic tool that connects to any database schema, ingests company-specific metric definitions, and returns verified SQL answers to non-technical users with a two-layer reflection check. Demo dataset: TPC-H. Target users: analytics engineers (setup), PMs and data scientists (query).

## Stack and Environment

- Python project using `openai`, `duckdb`, `pandas`, and `langgraph`.
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
- `graph.py`: LangGraph state machine that routes SQL generation, execution, reflection, validation, explanation, and terminal output.
- `main.py`: Demo pipeline runner that wires the agent functions together and runs example questions.
- `requirements.txt`: Python dependencies for OpenAI, DuckDB, pandas, and LangGraph.
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
- Returns `{"success": True, "sql": sql, "data": DataFrame, "error": ""}` on success.
- Returns `{"success": False, "sql": sql, "data": None, "error": error_message}` on failure.
- No LLM calls inside `run_sql()` ever.

`reflect_sql(conn, context, question, sql, error, attempt) -> dict`

- `conn`: active DuckDB connection.
- `context`: structured context string from `load_context()`.
- `question`: original plain English user question.
- `sql`: SQL from the previous failed or semantically wrong attempt.
- `error`: DuckDB error message or semantic failure description from `validate_result()`.
- `attempt`: current attempt number, starting at `1` after a failed attempt.
- Returns a consistent dict with `success`, `sql`, `data`, `error`, `attempts`, and `message`.
- On successful reflection, `message` is `""`.
- On max-attempt failure, `data` is `None` and `message` explains that maximum reflection attempts were reached.
- The `error` argument accepts both DuckDB error messages (technical failure) and semantic failure descriptions from `validate_result()`. It is not limited to DuckDB errors.

`validate_result(question, sql, df, context) -> dict`

- `question`: original plain English user question.
- `sql`: SQL that produced the result.
- `df`: pandas DataFrame containing the query result.
- `context`: structured context string from `load_context()`.
- Returns `{"valid": True, "reason": ""}` when valid.
- Returns `{"valid": False, "reason": reason}` when invalid.
- Runs Python checks first: zero rows, all-null numeric columns, and negative value warnings.
- Runs one LLM semantic check only if Python checks pass.

`generate_sql(context, question) -> str`

- `context`: structured context string returned by `load_context()`.
- `question`: plain English analytics question from the user.
- Returns a raw DuckDB SQL string with no markdown formatting or explanation.
- If the question refers to dates outside the data range, returns a string starting with `OUT_OF_RANGE:` instead of SQL. This is a contract with `generate_sql_node`, which must check for this prefix before routing to `run_sql`.

`explain_result(question, sql, df, context) -> str`

- `question`: original plain English user question.
- `sql`: SQL that produced the verified result.
- `df`: pandas DataFrame containing the verified result.
- `context`: structured context string from `load_context()`.
- Returns a plain English explanation string under 150 words for non-technical users.

### `graph.py`

`AgentState`

- Flat `TypedDict` containing per-question graph state: `question`, `sql`, `out_of_range`, `attempt`, `success`, `data`, `error`, `valid`, `validation_reason`, `explanation`, and `final_answer`.

`build_graph(conn, context) -> CompiledStateGraph`

- `conn`: active DuckDB connection captured by node closures.
- `context`: structured context string captured by node closures.
- Builds and compiles the LangGraph state machine.
- Nodes: `generate_sql`, `run_sql`, `reflect`, `validate`, `explain`, `output`, and `failure`.
- Conditional routers decide whether to execute SQL, reflect, validate, explain, fail, or exit early.

### `main.py`

`run_question(graph, question) -> None`

- `graph`: compiled LangGraph graph from `build_graph(conn, context)`.
- `question`: plain English analytics question to run.
- Initializes full graph state, streams `graph.stream(..., stream_mode="values")`, logs reflection attempts, and prints `final_answer`.
- Catches graph-level exceptions and prints a terminal error message.

At module runtime, `main.py` initializes the database, date range, context, and graph once, then runs the three demo questions.

## Graph Architecture

`graph.py` wraps the agent functions in a LangGraph `StateGraph`. These implementation decisions are intentional:

- Flat state, no nested dicts: `AgentState` keeps every value at the top level so node returns are simple partial state updates, routing conditions are easy to inspect, and UI integrations can read final state without unpacking nested objects.
- Factory pattern with closure: `build_graph(conn, context)` captures the DuckDB connection and loaded context once, so graph nodes do not need to pass heavy runtime dependencies through state. State stays focused on per-question data.
- Direct `state["key"]` access in routers: required routing fields are initialized in `main.py` before `graph.stream()`. Direct access makes missing state fail fast instead of silently routing based on defaults.
- Reflection accepts technical and semantic errors: the same `reflect` node handles DuckDB execution failures from `run_sql()` and semantic validation failures from `validate_result()`, because both require the same action: rewrite SQL using the previous query plus a problem description.
- `output_node` and `failure_node` are temporary terminal-formatting nodes: they produce `final_answer` for the command-line demo. For Streamlit, remove these nodes and render `state["data"]`, `state["explanation"]`, and `state["error"]` directly in the UI layer.
- `MemorySaver` was removed: this demo runs each question independently and does not need checkpoint persistence. Re-add `MemorySaver` or another checkpointer when supporting pause/resume, human approval steps, long-running sessions, or multi-turn threaded conversations.

## Pipeline Flow

1. `setup_database()` creates the in-memory TPC-H database and runtime aggregate tables.
2. `get_date_range(conn)` discovers the available order date range.
3. `load_context(min_date, max_date)` combines company context files with data range rules.
4. `build_graph(conn, context)` compiles the LangGraph workflow.
5. `run_question(graph, question)` initializes flat graph state and streams graph execution.
6. `generate_sql` produces SQL or an `OUT_OF_RANGE:` message.
7. Out-of-range questions set `final_answer` and route directly to `END`.
8. In-range SQL routes through `run_sql`, `reflect`, `validate`, `explain`, `output`, or `failure` based on conditional edges and `MAX_ATTEMPTS`.

## Conventions

- No LLM calls inside `load_context()`, `setup_database()`, `get_date_range()`, or `run_sql()` ever.
- Context files are the only thing that changes between company deployments; `agent.py` never hardcodes business rules.
- Never change function signatures without updating all callers: `load_context`, `run_sql`, `reflect_sql`, `validate_result`, and `explain_result`.
- Always run `python main.py` to verify changes work.

## Known Limitations

- `agg_daily_sales` and `agg_monthly_sales` are virtual tables built at runtime; they are not in `schema.sql`.
- Relative time references such as "last year" and "recent" are intentionally flagged as out of range. This is a design decision, not a bug.
- Semantic validation relies on LLM judgment, which may occasionally pass incorrect results.
