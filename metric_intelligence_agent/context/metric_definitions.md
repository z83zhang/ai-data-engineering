# Metric Definitions

These are the canonical metric definitions for SQL generation over the TPC-H warehouse context. Each metric includes the business definition, canonical SQL, source tables, and caveats.

## Ambiguity Handling

If a question mentions "region" or "geography" without specifying customer or supplier, default to customer region and note the assumption in the explanation. Ask for clarification if the context suggests supplier analysis.

## Revenue

Canonical definition:

- Net merchandise revenue after line-level discount, before tax.
- Formula: `SUM(l_extendedprice * (1 - l_discount))`

Canonical SQL:

```sql
SELECT
  SUM(l.l_extendedprice * (1 - l.l_discount)) AS revenue
FROM lineitem AS l
JOIN orders AS o
  ON l.l_orderkey = o.o_orderkey
WHERE o.o_orderstatus <> 'C';
```

Table(s) to use:

- `lineitem`
- `orders`
- Join condition: `lineitem.l_orderkey = orders.o_orderkey`

Caveats:

- Do not use `orders.o_totalprice` as canonical revenue.
- Do not include tax unless explicitly asked.
- Exclude cancelled orders by default unless the user explicitly asks for all orders.
- For revenue by order date, join `lineitem` to `orders`, group by `o_orderdate`.
- For revenue by customer region: `lineitem` -> `orders` -> `customer` -> `nation` -> `region`.
- For revenue by supplier region: `lineitem` -> `supplier` -> `nation` -> `region`.
- Never mix customer and supplier geography paths in one query without aliasing `nation` and `region` tables explicitly.

## Order Volume

Canonical definition:

- Count of distinct customer orders.
- Formula: `COUNT(DISTINCT o_orderkey)`

Canonical SQL:

```sql
SELECT
  COUNT(DISTINCT o_orderkey) AS order_volume
FROM orders
WHERE o_orderstatus <> 'C';
```

Table(s) to use:

- `orders`

Caveats:

- Never count `lineitem` rows as order volume; that counts order lines.
- If joining `orders` to `lineitem`, still use `COUNT(DISTINCT orders.o_orderkey)`.
- Exclude cancelled orders by default unless the user explicitly asks for all orders.
- Use `o_orderdate` as the canonical date for trends.

## Average Order Value

Canonical definition:

- Revenue divided by order volume; measures spend per order.
- Formula: `SUM(l_extendedprice * (1 - l_discount)) / COUNT(DISTINCT o_orderkey)`

Canonical SQL:

```sql
SELECT
  SUM(l.l_extendedprice * (1 - l.l_discount))
    / NULLIF(COUNT(DISTINCT o.o_orderkey), 0) AS average_order_value
FROM lineitem AS l
JOIN orders AS o
  ON l.l_orderkey = o.o_orderkey
WHERE o.o_orderstatus <> 'C';
```

Table(s) to use:

- `lineitem`
- `orders`
- Join condition: `lineitem.l_orderkey = orders.o_orderkey`

Caveats:

- Always compute from the same grain in a single query; never divide two separately computed numbers from different queries.
- Exclude cancelled orders (`o_orderstatus <> 'C'`) for consistency.

## Revenue by Customer Region

Canonical definition:

- Revenue attributed to the customer's geographic region.
- Formula: `SUM(l_extendedprice * (1 - l_discount))` grouped by `r_name`.

Canonical SQL:

```sql
SELECT
  customer_region.r_name AS customer_region,
  SUM(l.l_extendedprice * (1 - l.l_discount)) AS revenue
FROM lineitem AS l
JOIN orders AS o
  ON l.l_orderkey = o.o_orderkey
JOIN customer AS c
  ON o.o_custkey = c.c_custkey
JOIN nation AS customer_nation
  ON c.c_nationkey = customer_nation.n_nationkey
JOIN region AS customer_region
  ON customer_nation.n_regionkey = customer_region.r_regionkey
WHERE o.o_orderstatus <> 'C'
GROUP BY customer_region.r_name;
```

Table(s) to use:

- `lineitem`
- `orders`
- `customer`
- `nation`
- `region`
- Join path: `lineitem` -> `orders` -> `customer` -> `nation` -> `region`

Caveats:

- This is customer geography; do not use the supplier join path.
- Alias `nation` as `customer_nation` and `region` as `customer_region` if supplier geography also appears in the same query.
- Use `o_orderdate` for time filtering, not `l_shipdate`.

## Revenue by Supplier Region

Canonical definition:

- Revenue attributed to the supplier's geographic region.
- Formula: `SUM(l_extendedprice * (1 - l_discount))` grouped by `r_name`.

Canonical SQL:

```sql
SELECT
  supplier_region.r_name AS supplier_region,
  SUM(l.l_extendedprice * (1 - l.l_discount)) AS revenue
FROM lineitem AS l
JOIN supplier AS s
  ON l.l_suppkey = s.s_suppkey
JOIN nation AS supplier_nation
  ON s.s_nationkey = supplier_nation.n_nationkey
JOIN region AS supplier_region
  ON supplier_nation.n_regionkey = supplier_region.r_regionkey
GROUP BY supplier_region.r_name;
```

With time filtering:

```sql
SELECT
  supplier_region.r_name AS supplier_region,
  SUM(l.l_extendedprice * (1 - l.l_discount)) AS revenue
FROM lineitem AS l
JOIN supplier AS s
  ON l.l_suppkey = s.s_suppkey
JOIN orders AS o
  ON l.l_orderkey = o.o_orderkey
JOIN nation AS supplier_nation
  ON s.s_nationkey = supplier_nation.n_nationkey
JOIN region AS supplier_region
  ON supplier_nation.n_regionkey = supplier_region.r_regionkey
WHERE o.o_orderstatus <> 'C'
  AND o.o_orderdate BETWEEN '1995-01-01' AND '1995-12-31'
GROUP BY supplier_region.r_name;
```

Table(s) to use:

- `lineitem`
- `supplier`
- `nation`
- `region`
- Join path: `lineitem` -> `supplier` -> `nation` -> `region`

Caveats:

- This is supplier geography; do not use the customer join path.
- Alias `nation` as `supplier_nation` and `region` as `supplier_region` if customer geography also appears in the same query.
- For time filtering by order date, join back to `orders` via `lineitem.l_orderkey = orders.o_orderkey` and filter on `o_orderdate`.
- Revenue by supplier region and revenue by customer region will return different numbers for the same time period; both are correct, they measure different things.
