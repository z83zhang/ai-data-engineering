# SQL Intelligence Agent Project Prompt

Create a Python project folder called `sql_intelligence_agent` with the following structure:

```text
sql_intelligence_agent/
|-- context/
|   |-- table_catalog.md
|   |-- metric_definitions.md
|   `-- schema.sql
|-- agent.py
|-- main.py
`-- requirements.txt
```

## Project Rules

- Leave `agent.py` and `main.py` empty for now.
- `requirements.txt` should include:
  - `openai`
  - `duckdb`
  - `pandas`
- Populate the context files with TPC-H data described as a realistic layered warehouse.

## Warehouse Layers

1. Aggregated layer: pre-computed daily/monthly metrics. Use this first.
2. Fact layer: `orders` and `lineitem`. Use when the aggregated layer lacks granularity.
3. Dimension layer: `customer`, `supplier`, `nation`, and `region`.

## Context Files

### `table_catalog.md`

Describe each table, including:

- Which warehouse layer it belongs to
- When to use it
- Caveats
- Columns for each table

### `metric_definitions.md`

Each metric must include:

- Canonical definition
- Canonical SQL
- Which table(s) to use
- Caveats

#### Revenue

- Definition: net merchandise revenue after line-level discount, before tax
- Formula: `SUM(l_extendedprice * (1 - l_discount))`
- Source table: `lineitem`
- Caveats:
  - Do not use `orders.o_totalprice` as canonical revenue.
  - Do not include tax unless explicitly asked.
  - For revenue by order date, join `lineitem` to `orders`, group by `o_orderdate`.
  - For revenue by customer region: `lineitem` -> `orders` -> `customer` -> `nation` -> `region`.
  - For revenue by supplier region: `lineitem` -> `supplier` -> `nation` -> `region`.
  - Never mix customer and supplier geography paths in one query without aliasing `nation` and `region` tables explicitly.

#### Order Volume

- Definition: count of distinct customer orders
- Formula: `COUNT(DISTINCT o_orderkey)`
- Source table: `orders`
- Caveats:
  - Never count `lineitem` rows as order volume; that counts order lines.
  - If joining `orders` to `lineitem`, still use `COUNT(DISTINCT orders.o_orderkey)`.
  - Use `o_orderdate` as the canonical date for trends.

#### Average Order Value

- Definition: revenue divided by order volume; measures spend per order
- Formula: `SUM(l_extendedprice * (1 - l_discount)) / COUNT(DISTINCT o_orderkey)`
- Source tables: `lineitem` joined to `orders` on `l_orderkey = o_orderkey`
- Caveats:
  - Always compute from the same grain in a single query; never divide two separately computed numbers from different queries.
  - Exclude cancelled orders (`o_orderstatus <> 'C'`) for consistency.

#### Revenue by Customer Region

- Definition: revenue attributed to the customer's geographic region
- Formula: `SUM(l_extendedprice * (1 - l_discount))` grouped by `r_name`
- Join path: `lineitem` -> `orders` -> `customer` -> `nation` -> `region`
- Caveats:
  - This is customer geography; do not use the supplier join path.
  - Alias `nation` as `customer_nation` and `region` as `customer_region` if supplier geography also appears in the same query.
  - Use `o_orderdate` for time filtering, not `l_shipdate`.

#### Revenue by Supplier Region

- Definition: revenue attributed to the supplier's geographic region
- Formula: `SUM(l_extendedprice * (1 - l_discount))` grouped by `r_name`
- Join path: `lineitem` -> `supplier` -> `nation` -> `region`
- Caveats:
  - This is supplier geography; do not use the customer join path.
  - Alias `nation` as `supplier_nation` and `region` as `supplier_region` if customer geography also appears in the same query.
  - Revenue by supplier region and revenue by customer region will return different numbers for the same time period; both are correct because they measure different things.

### `schema.sql`

Include TPC-H `CREATE TABLE` statements for:

- `orders`
- `lineitem`
- `customer`
- `supplier`
- `nation`
- `region`

Use standard TPC-H column names and types.
