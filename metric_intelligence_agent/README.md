# Metric Intelligence Agent

An agentic tool that answers data questions for product managers and data scientists — in plain English, no SQL required.

## The Problem

At companies with mature data foundations, PMs and data scientists ask questions like "What was DAU last week?" or "What's revenue by region?" These require knowing the schema, metric definitions, and tribal knowledge about which tables to trust. This tool answers them automatically and checks its own work.

## How It Works

1. Analytics engineer provides three context files.
2. User asks question in plain English.
3. Agent writes SQL and runs it against the database.
4. If SQL fails or result looks wrong, agent reflects and rewrites automatically up to `MAX_ATTEMPTS` times.
5. User gets verified answer with plain English explanation.

## Two-Layer Reflection

Layer 1: Technical -- did DuckDB execute without error?

Layer 2: Semantic -- does the result answer the question? Are values plausible given metric definitions?

If either fails, agent rewrites SQL automatically.

## Real Output Examples

Example 1: successful query

User: "What is total revenue by customer region?"

✅ Answer verified — 1 attempt(s)

Result:

```text
  customer_region       revenue
0          AFRICA  4.104594e+09
1          EUROPE  4.082607e+09
2     MIDDLE EAST  4.172220e+09
3            ASIA  4.120877e+09
4         AMERICA  4.054775e+09
```

Explanation:

The Middle East leads with ~$4.17B, followed by Asia at $4.12B. Revenue is net merchandise value after discounts, excluding cancelled orders. Defaulted to customer region since geography was unspecified.

Example 2: out-of-range question

User: "What was revenue last year?"

⚠️ This dataset only contains data from 1992-01-01 to 1998-08-02. Your question refers to a time period outside this range. Please specify a year between 1992 and 1998.

## Stack

- Python 3.13
- DuckDB, an in-memory analytics database
- OpenAI `gpt-4o`
- pandas

## Setup

Mac/Linux terminal:

```bash
export OPENAI_API_KEY=your_key_here
```

Windows Command Prompt:

```bat
set OPENAI_API_KEY=your_key_here
```

Windows Git Bash:

```bash
export OPENAI_API_KEY=your_key_here
```

Then:

```bash
git clone https://github.com/z83zhang/ai-data-engineering
cd ai-data-engineering/metric_intelligence_agent
pip install -r requirements.txt
python main.py
```

## Generalization

Replace the three context files with your company's own:

- `table_catalog.md` -> your data layer documentation
- `metric_definitions.md` -> your canonical metric defs
- `schema.sql` -> your table schemas

Agent code does not change. Only context files do.

For dbt users, `load_context()` can be extended to parse `manifest.json` and `schema.yml` directly. The agent interface stays the same.

## Scaling to Production

This demo uses DuckDB and static context files for portability. A production deployment at a data-mature company would address four layers:

**Context retrieval -- replace static files with live retrieval:**

- RAG over a data catalog (Datahub, Amundsen, Glean) so only the most relevant tables and definitions are passed into each prompt -- keeps context small regardless of data foundation size.
- Two-step table selection: first identify candidate tables from the catalog, then retrieve their full schema and metric definitions.
- dbt metrics layer or semantic layer (Cube, Looker) as the canonical source of metric definitions instead of markdown files.

**Query execution -- replace DuckDB with your query engine:**

- Point `run_sql()` at Presto, BigQuery, Snowflake, or Hive.
- Add pre-execution validation: check for partition filters, prevent full table scans, enforce compute quotas before any query runs.
- Set query timeouts and return partial results with a warning rather than failing silently on long-running queries.

**Performance -- make it usable at scale:**

- Cache generated SQL and results for common questions -- the same metric question gets asked repeatedly by different PMs.
- Prefer pre-aggregated tables for speed, already implemented in this demo via `agg_daily_sales` and `agg_monthly_sales`.

**Governance -- required at any regulated or data-mature company:**

- Respect user permissions when selecting tables -- a PM should not be able to query raw logging tables or PII data.
- Audit log every query: who asked, what SQL ran, what data was returned -- a compliance requirement, not optional.

The agent logic -- reflection loop, semantic validation, plain English explanation -- works the same at any scale. The context, execution, performance, and governance layers are what change.

## What's Next

- Refactor pipeline into LangGraph with named nodes and explicit conditional routing.
- Evaluation layer: log all attempts, scored test suite, failure pattern analysis.
- Direct database connections via connection string.
- Streamlit UI with setup mode and query mode.

## Production Extensions

For teams deploying this in a real data environment:

- dbt `manifest.json` ingestion to replace context files.
- Slack integration for PM self-serve queries.
- Company wiki URL ingestion for metric definitions.
