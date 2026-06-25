# Table Catalog

This project models TPC-H as a realistic layered analytical warehouse. Query planning should prefer the highest usable layer first, then fall back to lower-grain tables only when the question needs more detail.

## Layer Selection

1. Aggregated layer: pre-computed daily and monthly metrics. Use first for KPI summaries, trends, dashboards, and common rollups.
2. Fact layer: `orders` and `lineitem`. Use when the aggregated layer lacks the requested grain, filter, or metric logic.
3. Dimension layer: `customer`, `supplier`, `nation`, and `region`. Use to describe, filter, and group facts or aggregates.

## Aggregated Layer

The starter `schema.sql` contains the standard TPC-H base tables only, but agents should assume the warehouse may expose pre-computed aggregate tables such as the following.

### `agg_daily_sales`

Layer: Aggregated

Grain: One row per calendar day, commonly keyed by order date.

Columns:

- `order_date` DATE
- `revenue` DECIMAL
- `order_volume` INTEGER
- `discount_rate` DECIMAL

Use when:

- A question asks for daily revenue, order volume, discount rate, or trend lines.
- The requested filters are available in the aggregate table.
- The user wants a fast KPI answer rather than row-level auditability.

Caveats:

- Confirm which date the table uses. Daily sales should normally use `orders.o_orderdate`.
- Do not use this table if the user asks for line-level attributes such as ship mode, return flag, supplier, or receipt-date analysis unless those columns are present.
- If aggregate values disagree with the canonical metric definitions, the definitions in `metric_definitions.md` are the source of truth.

### `agg_monthly_sales`

Layer: Aggregated

Grain: One row per calendar month, commonly keyed by the month of `orders.o_orderdate`.

Columns:

- `order_year` INTEGER
- `order_month` INTEGER
- `revenue` DECIMAL
- `order_volume` INTEGER
- `discount_rate` DECIMAL

Use when:

- A question asks for monthly revenue, order volume, discount rate, month-over-month change, or rolling monthly summaries.
- The requested grain is month or coarser and no unavailable detail filters are needed.

Caveats:

- Avoid re-aggregating averages incorrectly. For discount rate, prefer stored numerator/denominator fields if available; otherwise fall back to facts.
- Do not use monthly aggregates for daily or order-level questions.

## Fact Layer

Use fact tables for exact canonical metric calculation and detail-level analysis.

### `orders`

Layer: Fact

Grain: One row per customer order.

Use when:

- Counting order volume.
- Filtering or grouping by order date, order status, order priority, clerk, or shipping priority.
- Joining orders to customers for customer segment or customer geography.

Caveats:

- Do not calculate canonical revenue from `orders.o_totalprice`; use `lineitem`.
- After joining to `lineitem`, use `COUNT(DISTINCT o_orderkey)` for order volume to avoid counting order lines.
- Treat cancelled orders as excluded when a metric definition says so.

Primary key: `o_orderkey`

Important joins:

- `orders.o_orderkey = lineitem.l_orderkey`
- `orders.o_custkey = customer.c_custkey`

### `lineitem`

Layer: Fact

Grain: One row per order line, identified by `l_orderkey` and `l_linenumber`.

Use when:

- Calculating revenue, discounts, quantities, tax, returns, line status, shipping, or supplier-level measures.
- The question needs ship date, commit date, receipt date, ship mode, return flag, or supplier attribution.
- The aggregated layer does not contain the requested level of detail.

Caveats:

- Revenue is line-level: `SUM(l_extendedprice * (1 - l_discount))`.
- `l_discount` is a decimal fraction, so `0.05` means 5%.
- Join to `orders` when order date, customer, customer segment, order status, or cancellation exclusion is needed.

Important joins:

- `lineitem.l_orderkey = orders.o_orderkey`
- `lineitem.l_suppkey = supplier.s_suppkey`

## Dimension Layer

Use dimensions to filter cohorts, label results, and group facts or aggregates. Dimensions should not be used alone for KPI metrics.

### `customer`

Layer: Dimension

Grain: One row per customer.

Use when:

- Grouping or filtering by customer, customer market segment, customer account balance, or customer nation.
- Answering customer geography questions through `nation` and `region`.

Caveats:

- `c_acctbal` is an account balance, not revenue.
- Customer geography is different from supplier geography; choose the join path that matches the question.

Primary key: `c_custkey`

Important joins:

- `customer.c_custkey = orders.o_custkey`
- `customer.c_nationkey = nation.n_nationkey`

### `supplier`

Layer: Dimension

Grain: One row per supplier.

Use when:

- Grouping or filtering line-level facts by supplier, supplier nation, or supplier region.
- Answering sourcing, fulfillment, or supplier contribution questions.

Caveats:

- `s_acctbal` is an account balance, not revenue.
- Supplier geography requires the supplier join path, not the customer join path.

Primary key: `s_suppkey`

Important joins:

- `supplier.s_suppkey = lineitem.l_suppkey`
- `supplier.s_nationkey = nation.n_nationkey`

### `nation`

Layer: Dimension

Grain: One row per nation.

Use when:

- Grouping customer or supplier metrics by country.
- Filtering to a named nation.

Caveats:

- `nation` can join to both `customer` and `supplier`. Use clear aliases such as `customer_nation` and `supplier_nation` when both paths appear in one query.

Primary key: `n_nationkey`

Important join:

- `nation.n_regionkey = region.r_regionkey`

### `region`

Layer: Dimension

Grain: One row per region.

Use when:

- Rolling nation-level geography up to broad regions such as AMERICA, ASIA, EUROPE, AFRICA, or MIDDLE EAST.
- Filtering to a regional market.

Caveats:

- Region metrics inherit either customer geography or supplier geography depending on the join path.

Primary key: `r_regionkey`

## Query Guidance

- Use aggregated daily or monthly tables first when their grain and available dimensions answer the question.
- Use `lineitem` for revenue and discount calculations.
- Use `orders` for order volume and order-date trends.
- Join facts to `customer`, `supplier`, `nation`, and `region` only when the question needs those attributes.
- Avoid mixing customer geography and supplier geography without explicitly labeling each path.
