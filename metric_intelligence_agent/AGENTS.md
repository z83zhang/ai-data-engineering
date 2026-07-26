# Metric Intelligence Agent

## Project Purpose

A generalized agentic tool that connects to any database schema, ingests company-specific metric definitions, and returns verified SQL answers to non-technical users with a two-layer reflection check. Demo dataset: TPC-H. Target users: analytics engineers (setup), PMs and data scientists (query).

## Stack and Environment

- Python project using `openai`, `duckdb`, `pandas`, and `langgraph`.
- OpenAI chat completions use `gpt-4o`.
- DuckDB runs in memory with the TPC-H extension loaded at runtime.
- Required environment variable:
  - `OPENAI_API_KEY`: used to initialize the OpenAI client. `agent.py` raises `ValueError` at import time if it is missing.
- Optional environment variable:
  - `MAX_REFLECTION_ATTEMPTS`: configures reflection retry count.
- `MAX_ATTEMPTS` defaults to `3` from `MAX_REFLECTION_ATTEMPTS`.
- All LLM calls use `temperature=0` for determinism.

## Project Structure

- `agent.py`: Database setup, context loading, SQL generation, execution, reflection, validation, explanation, token accounting from OpenAI responses.
- `graph.py`: LangGraph state machine with flat `AgentState`, conditional routing, token totals, and terminal answer formatting.
- `main.py`: Demo entry point that initializes database/context/graph/eval logging once, then runs three example questions.
- `utils.py`: Shared dependency-free utilities; currently the single source of truth for `compute_cost()`.
- `requirements.txt`: Python dependencies for OpenAI, DuckDB, pandas, and LangGraph.
- `.gitignore`: Local ignore rules, including `eval.db`.
- `context/table_catalog.md`: Warehouse layer selection rules, table descriptions, joins, aggregate table dimensions, and correctness caveats.
- `context/metric_definitions.md`: Canonical metric formulas, source-table guidance, geography ambiguity rules, and metric caveats.
- `context/schema.sql`: Base TPC-H schema definitions.
- `docs/file_dependency.md`: Mermaid file dependency graph.
- `docs/data_flow.md`: Mermaid runtime data flow graph.
- `eval/__init__.py`: Marks `eval` as an importable package.
- `eval/logger.py`: Persistent DuckDB eval log setup, layer detection, and run logging.
- `eval/runner.py`: Shared graph runner used by `main.py` and `eval/run_eval.py`.
- `eval/test_suite.py`: Thirteen evaluation cases covering wrong layer, wrong join path, wrong metric formula, and out-of-range behavior.
- `eval/run_eval.py`: Full evaluation runner with scoring, logging, pass-rate summaries, cost totals, and failure reporting.
- `eval.db`: Local persistent DuckDB evaluation database created at runtime and gitignored.

## Function Reference

### `agent.py`

`load_context(min_date, max_date) -> str`

- Reads `context/table_catalog.md`, `context/metric_definitions.md`, and `context/schema.sql`.
- Appends a data-range section using `min_date` and `max_date`.
- Raises `FileNotFoundError` if a required context file is missing.
- No LLM calls inside `load_context()` ever.

`setup_database() -> duckdb.DuckDBPyConnection`

- Creates an in-memory DuckDB database.
- Installs and loads the TPC-H extension.
- Generates TPC-H data at scale factor `0.1`.
- Builds `agg_daily_sales` and `agg_monthly_sales` with customer region, customer nation, market segment, revenue, order volume, and discount rate.
- No LLM calls inside `setup_database()` ever.

`get_date_range(conn) -> tuple[str, str]`

- Queries `orders` for minimum and maximum `o_orderdate`.
- Returns `(min_date, max_date)` as `YYYY-MM-DD` strings.
- No LLM calls inside `get_date_range()` ever.

`run_sql(conn, sql) -> dict`

- Executes SQL against DuckDB.
- Success shape: `{"success": True, "sql": sql, "data": DataFrame, "error": ""}`.
- Failure shape: `{"success": False, "sql": sql, "data": None, "error": str}`.
- No LLM calls inside `run_sql()` ever.

`reflect_sql(conn, context, question, sql, error, attempt) -> dict`

- Uses the LLM to rewrite failed or semantically invalid SQL, then immediately runs it with `run_sql()`.
- `error` accepts DuckDB execution errors and semantic failure descriptions from `validate_result()`.
- If `attempt >= MAX_ATTEMPTS`, returns failure without another LLM call and token counts of `0`.
- Success shape includes `success`, `sql`, `data`, `error`, `attempts`, `message`, `input_tokens`, and `output_tokens`.
- Failure shape uses the same keys; `message` explains when maximum reflection attempts were reached.

`validate_result(question, sql, df, context) -> dict`

- Runs Python checks first: zero rows, all-null numeric columns, and negative numeric value warnings.
- Calls one LLM semantic validator only if Python checks pass.
- Returns `valid`, `reason`, `input_tokens`, and `output_tokens`.
- Valid results use `{"valid": True, "reason": ""}`.

`generate_sql(context, question) -> tuple[str, int, int]`

- Generates raw DuckDB SQL from the context and plain English question.
- Strips markdown fences from model output.
- Returns `(sql_or_out_of_range_message, input_tokens, output_tokens)`.
- If the question refers to dates outside the data range, returns a string starting with `OUT_OF_RANGE:` instead of SQL. This is a contract with `generate_sql_node`, which must check the prefix before routing to `run_sql`.

`explain_result(question, sql, df, context) -> tuple[str, int, int]`

- Generates a plain English explanation under 150 words for a verified result.
- Returns `(explanation, input_tokens, output_tokens)`.

### `graph.py`

`AgentState`

- Flat `TypedDict` containing: `question`, `sql`, `out_of_range`, `attempt`, `success`, `data`, `error`, `valid`, `validation_reason`, `explanation`, `final_answer`, `total_input_tokens`, `total_output_tokens`, and `cost_usd`.
- `data` is `Optional[pd.DataFrame]`.

`build_graph(conn, context)`

- Captures `conn` and `context` in inner node closures.
- Builds and compiles a LangGraph `StateGraph`.
- Nodes: `generate_sql`, `run_sql`, `reflect`, `validate`, `explain`, `output`, and `failure`.
- Routers: `route_after_generate`, `route_after_run`, and `route_after_validate`.
- Accumulates real LLM input/output token counts in graph state.
- Computes `cost_usd` through `utils.compute_cost()` in final output/failure/out-of-range paths.

### `utils.py`

`compute_cost(input_tokens, output_tokens) -> float`

- Computes estimated `gpt-4o` cost using `$2.50` per 1M input tokens and `$10.00` per 1M output tokens.
- Has no project dependencies and is safe to import from agent, graph, or eval code.

### `eval/logger.py`

`setup_eval_db()`

- Opens or creates `metric_intelligence_agent/eval.db`.
- Ensures `query_log` exists with run metadata, final SQL, pass flags, timing, token counts, cost, run type, and human review fields.
- Returns the active DuckDB connection; caller closes it.

`detect_layer_used(sql) -> str`

- Returns `None` for empty SQL.
- Uses case-insensitive regex word boundaries.
- Returns `aggregated` for `agg_daily_sales` or `agg_monthly_sales`.
- Returns `fact` for `orders` or `lineitem`.
- Returns `dimension` for only dimension table names.
- Returns `unknown` otherwise.

`log_run(conn, final_state, response_time_ms, run_type="adhoc")`

- Validates `run_type` is `adhoc` or `eval`.
- Inserts one row into `query_log`.
- For out-of-range runs, stores `NULL` for SQL, technical pass, semantic pass, layer, and error.
- Reads `cost_usd` from `final_state`; does not recompute cost.
- Does not close the connection.

### `eval/runner.py`

`run_question(graph, eval_conn, question, run_type="adhoc", verbose=True)`

- Builds the full initial graph state, including zero token totals and `cost_usd`.
- Streams the graph with `stream_mode="values"` so each update contains full state.
- Generates an unused `_thread_id` for future checkpointer integration.
- Logs the run through `log_run()`.
- Returns final graph state.
- When `verbose=True`, prints the question, reflection attempts, final answer, token totals, and estimated cost.

### `eval/run_eval.py`

`score_result(final_state, test_case) -> dict`

- Scores row count, required columns, expected value, detected layer, and out-of-range behavior.
- Value checks use the first numeric non-grouping column; `expected_value_aggregation="first"` spot-checks the first ordered row.
- `None` expected checks do not count as failures.
- Returns pass/fail booleans plus failure mode, question, and notes.

`run_eval()`

- Initializes database, date range, context, graph, and eval DB once.
- Runs every `TEST_CASES` question through `eval.runner.run_question(..., run_type="eval", verbose=False)`.
- Logs every run, scores every final state, prints per-case pass/fail lines, sleeps between cases, and prints summary pass rates, average attempts, total cost, and failed checks.
- Closes `eval_conn` in a `finally` block.

### `main.py`

- Initializes `conn`, date range, context, graph, and `eval_conn` once.
- Runs three demo questions through `eval.runner.run_question()`.
- Catches per-question graph errors and closes `eval_conn` in `finally`.

## Graph Architecture

`graph.py` wraps the agent functions in a LangGraph `StateGraph`. These implementation decisions are intentional:

- Flat state, no nested dicts: `AgentState` keeps every value at the top level so node returns are simple partial state updates, routing conditions are easy to inspect, and UI integrations can read final state without unpacking nested objects.
- Factory pattern with closure: `build_graph(conn, context)` captures the DuckDB connection and loaded context once, so graph nodes do not need to pass heavy runtime dependencies through state. State stays focused on per-question data.
- Direct `state["key"]` access in routers: required routing fields are initialized before graph execution. Direct access makes missing state fail fast instead of silently routing based on defaults.
- Reflection accepts technical and semantic errors: the same `reflect` node handles DuckDB execution failures from `run_sql()` and semantic validation failures from `validate_result()`, because both require the same action: rewrite SQL using the previous query plus a problem description.
- `output_node` and `failure_node` are temporary terminal-formatting nodes: they produce `final_answer` for the command-line demo. For Streamlit, remove these nodes and render `state["data"]`, `state["explanation"]`, and `state["error"]` directly in the UI layer.
- `MemorySaver` was removed: this demo runs each question independently and does not need checkpoint persistence. Re-add `MemorySaver` or another checkpointer when supporting pause/resume, human approval steps, long-running sessions, or multi-turn threaded conversations.

## Evaluation Layer

- `eval.db` is a persistent local DuckDB database for run history; it is created by `setup_eval_db()` and ignored by git.
- `query_log` records one row per adhoc or eval run: run ID, timestamp, question, final SQL, attempt count, technical/semantic pass flags, out-of-range flag, detected layer, last error, latency, token counts, cost, run type, and human review placeholders.
- `eval.runner.run_question()` is the shared execution path for both `main.py` and `eval/run_eval.py`, so adhoc demos and suite runs get consistent initial state, logging, token accounting, and cost reporting.
- `eval/test_suite.py` defines 13 cases across four failure modes: wrong layer, wrong join path, wrong metric formula, and out of range.
- `eval/run_eval.py` scores final state against row count, required columns, expected numeric values, expected layer, and out-of-range behavior.
- Layer detection is heuristic and SQL-text based. It is good for evaluation feedback, not a database query planner.
- Human review fields exist in `query_log`, but no UI currently writes `human_rating` or `human_notes`.

## Pipeline Flow

1. `setup_database()` creates the in-memory TPC-H database and runtime aggregate tables.
2. `get_date_range(conn)` discovers the available order date range.
3. `load_context(min_date, max_date)` combines company context files with data range rules.
4. `build_graph(conn, context)` compiles the LangGraph workflow.
5. `setup_eval_db()` opens local evaluation logging.
6. `run_question(graph, eval_conn, question)` initializes flat graph state and streams graph execution.
7. `generate_sql` produces SQL or an `OUT_OF_RANGE:` message.
8. Out-of-range questions set `final_answer`, compute cost, and route directly to `END`.
9. In-range SQL routes through `run_sql`, `reflect`, `validate`, `explain`, `output`, or `failure` based on conditional edges and `MAX_ATTEMPTS`.
10. `log_run()` writes final state metadata to `eval.db`.

## Conventions

- No LLM calls inside `load_context()`, `setup_database()`, `get_date_range()`, or `run_sql()` ever.
- Context files are the only thing that changes between company deployments; `agent.py` never hardcodes business rules.
- `utils.py` is the single source of truth for `compute_cost()` and must remain dependency-free.
- Never change function signatures without updating all callers in `graph.py`, `main.py`, `eval/runner.py`, and `eval/run_eval.py`.
- Always run `python main.py` to verify end-to-end changes when LLM/API access is available.
- For lightweight verification without API calls, run `python -m py_compile agent.py graph.py main.py utils.py eval/logger.py eval/runner.py eval/test_suite.py eval/run_eval.py`.

## Known Limitations

- `agg_daily_sales` and `agg_monthly_sales` are runtime tables built in DuckDB; they are not in `schema.sql`.
- Aggregate tables include customer geography and market segment, but supplier geography is intentionally excluded to avoid dimensional explosion.
- Demo aggregate tables are unpartitioned in-memory DuckDB tables; production warehouses should partition aggregate tables by date.
- Relative time references such as "last year", "recent", "current", "latest", and "this quarter" are intentionally flagged as out of range when they resolve outside 1992-01-01 to 1998-08-02.
- Semantic validation relies on LLM judgment, which may occasionally pass incorrect results or reject valid results.
- Evaluation scoring checks expected values using the first numeric non-grouping column; unusual result column ordering can affect value checks.
- `eval.db` is local-only and gitignored; sharing evaluation history requires exporting or copying it manually.
