# Table Catalog

## Layer Selection

Always prefer the highest available layer that can correctly answer the question. Use the decision rules below.

### Use aggregated layer FIRST when ALL of these are true:

- The metric requested is revenue, order_volume, or discount_rate
- The grouping dimensions are any combination of: order_date, order_year, order_month, customer_region, customer_nation, market_segment
- No supplier geography is needed
- No line-level fields are needed (ship mode, return flag, receipt date, commit date, line status)
- No filters on columns not present in agg tables

### Use fact layer ONLY when ANY of these is true:

- Supplier geography is needed (supplier_region, supplier_nation)
- Line-level fields are needed
- Average order value requires exact computation -- sum revenue and order_volume from agg tables separately before dividing; do not divide pre-aggregated values directly
- Required filter or dimension is not in agg tables

### Use dimension layer ONLY for:

- Labels, filters, and cohort definitions
- Never alone for KPI metrics

### Key rule for ungrouped totals:

- "What is total revenue?" with no grouping -> sum revenue from agg_daily_sales or agg_monthly_sales
- "Daily revenue" with no dimension specified -> GROUP BY order_date only, SUM revenue across all customer_region x customer_nation x market_segment combinations per day.
- "Monthly revenue" with no dimension specified -> GROUP BY order_year, order_month only.
- "Annual revenue" with no dimension specified -> GROUP BY order_year only.
- Never go to lineitem for a metric that agg tables already have pre-computed

### Agg table available dimensions:

- agg_daily_sales: order_date, customer_region, customer_nation, market_segment
- agg_monthly_sales: order_year, order_month, customer_region, customer_nation, market_segment

## Aggregated Layer

Production note: In this demo agg tables are unpartitioned in-memory DuckDB. In production (Hive, BigQuery, Snowflake) these tables are partitioned by order_date -- each partition contains only that day's dimension combinations, enabling fast scans without full table reads even with additional dimensions. Supplier geography is excluded from agg tables to avoid combinatorial row explosion without partition pruning.

### `agg_daily_sales`

- Layer: aggregated
- Grain: `order_date`, `customer_region`, `customer_nation`, `market_segment`
- Columns: `order_date` DATE, `customer_region` VARCHAR, `customer_nation` VARCHAR, `market_segment` VARCHAR, `revenue` DECIMAL, `order_volume` INTEGER, `discount_rate` DECIMAL
- Use for: daily revenue/order volume/discount rate/trends; daily metrics by customer region, customer nation, market segment; filters available in table.
- Do not use for: supplier geography, ship mode, return flag, receipt/commit/ship date, line-level audit, unavailable filters.
- When a question asks for daily metrics with no dimension filter (e.g. "daily revenue in January"), GROUP BY order_date only and SUM across all dimension combinations. Do not return one row per date x region x nation x segment unless the question explicitly asks for a breakdown by those dimensions.
- Date: uses `orders.o_orderdate`.
- Metric definitions override aggregate values if conflict.

### `agg_monthly_sales`

- Layer: aggregated
- Grain: `order_year`, `order_month`, `customer_region`, `customer_nation`, `market_segment`
- Columns: `order_year` INTEGER, `order_month` INTEGER, `customer_region` VARCHAR, `customer_nation` VARCHAR, `market_segment` VARCHAR, `revenue` DECIMAL, `order_volume` INTEGER, `discount_rate` DECIMAL
- Use for: monthly/coarser revenue, order volume, discount rate, month-over-month, rolling monthly summaries; metrics by customer region, customer nation, market segment.
- Do not use for: daily/order-level questions, supplier geography, unavailable filters.
- Caveat: avoid re-aggregating averages incorrectly; for `discount_rate`, use facts if numerator/denominator not available.

## Fact Layer

### `orders`

- Layer: fact
- Grain: one row per customer order
- Primary key: `o_orderkey`
- Use for: order volume, order date/status/priority/clerk/ship priority filters, customer joins.
- Joins: `orders.o_orderkey = lineitem.l_orderkey`; `orders.o_custkey = customer.c_custkey`
- Caveats: revenue never from `orders.o_totalprice`; after joining to `lineitem`, order volume is `COUNT(DISTINCT o_orderkey)`; exclude cancelled orders when metric says so.

### `lineitem`

- Layer: fact
- Grain: one row per order line: `l_orderkey`, `l_linenumber`
- Use for: revenue, discount, quantity, tax, returns, line status, shipping, supplier metrics.
- Joins: `lineitem.l_orderkey = orders.o_orderkey`; `lineitem.l_suppkey = supplier.s_suppkey`
- Caveats: revenue formula `SUM(l_extendedprice * (1 - l_discount))`; `l_discount` is decimal fraction; join `orders` for order date/customer/status/cancellation exclusion.

## Dimension Layer

### `customer`

- Grain: one row per customer
- Primary key: `c_custkey`
- Use for: customer, `c_mktsegment`, account balance, customer nation/region.
- Joins: `customer.c_custkey = orders.o_custkey`; `customer.c_nationkey = nation.n_nationkey`
- Caveats: `c_acctbal` is not revenue; customer geography differs from supplier geography.

### `supplier`

- Grain: one row per supplier
- Primary key: `s_suppkey`
- Use for: supplier, supplier nation/region, sourcing/fulfillment/supplier contribution.
- Joins: `supplier.s_suppkey = lineitem.l_suppkey`; `supplier.s_nationkey = nation.n_nationkey`
- Caveats: `s_acctbal` is not revenue; supplier geography requires supplier join path.

### `nation`

- Grain: one row per nation
- Primary key: `n_nationkey`
- Use for: customer/supplier country grouping/filter.
- Join: `nation.n_regionkey = region.r_regionkey`
- Caveat: alias clearly when both paths appear, e.g. `customer_nation`, `supplier_nation`.

### `region`

- Grain: one row per region
- Primary key: `r_regionkey`
- Values: AFRICA, AMERICA, ASIA, EUROPE, MIDDLE EAST
- Use for: regional grouping/filter.
- Caveat: region metrics inherit customer or supplier path.

## Query Guidance

- Prefer aggregates when grain and dimensions fit.
- Use facts for supplier geography, line-level fields, unavailable aggregate filters, AOV exactness, or custom formulas.
- Use `lineitem` for revenue/discount calculations when facts required.
- Use `orders.o_orderdate` for canonical order-date trends.
- Avoid mixing customer and supplier geography without explicit aliases.
