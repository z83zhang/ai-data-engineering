# Metric Definitions

## Ambiguity

- If question says "region" or "geography" without customer/supplier: default to customer region.
- Note the customer-region assumption in explanation.
- Ask/clarify only if context strongly suggests supplier analysis.

## Revenue

- Definition: net merchandise revenue after line discount, before tax.
- Formula: `SUM(l_extendedprice * (1 - l_discount))`
- Exclude cancelled orders by default: `o_orderstatus <> 'C'`
- Never use `orders.o_totalprice` as canonical revenue.
- Do not include tax unless explicitly asked.
- Order-date trends use `orders.o_orderdate`.
- Customer geography path: `lineitem` -> `orders` -> `customer` -> `nation` -> `region`
- Supplier geography path: `lineitem` -> `supplier` -> `nation` -> `region`; join `orders` too when order date or cancellation exclusion needed.
- Do not mix customer/supplier geography without explicit aliases.

Canonical fact SQL:

```sql
SELECT SUM(l.l_extendedprice * (1 - l.l_discount)) AS revenue
FROM lineitem AS l
JOIN orders AS o ON l.l_orderkey = o.o_orderkey
WHERE o.o_orderstatus <> 'C';
```

## Order Volume

- Definition: count distinct customer orders.
- Formula: `COUNT(DISTINCT o_orderkey)`
- Exclude cancelled orders by default: `o_orderstatus <> 'C'`
- Never count `lineitem` rows as orders.
- If joined to `lineitem`, still use `COUNT(DISTINCT o.o_orderkey)`.
- Date: `orders.o_orderdate`.

Canonical SQL:

```sql
SELECT COUNT(DISTINCT o_orderkey) AS order_volume
FROM orders
WHERE o_orderstatus <> 'C';
```

## Average Order Value

- Definition: revenue / order volume.
- Formula: `SUM(l_extendedprice * (1 - l_discount)) / COUNT(DISTINCT o_orderkey)`
- Exclude cancelled orders.
- Prefer aggregated layer when monthly grain is available.
- Sum revenue and order_volume from agg_monthly_sales separately before dividing -- do not divide pre-aggregated averages directly.
- Fall back to fact layer only when aggregated layer lacks the required grain or filters.

Canonical SQL:

```sql
SELECT
  SUM(l.l_extendedprice * (1 - l.l_discount))
    / NULLIF(COUNT(DISTINCT o.o_orderkey), 0) AS average_order_value
FROM lineitem AS l
JOIN orders AS o ON l.l_orderkey = o.o_orderkey
WHERE o.o_orderstatus <> 'C';
```

## Revenue by Customer Region

- Metric: revenue grouped by customer `region.r_name AS customer_region`.
- Join path: `lineitem l` -> `orders o` -> `customer c` -> `nation customer_nation` -> `region customer_region`
- Filter: `o.o_orderstatus <> 'C'`
- Time filter: `o.o_orderdate`, not `l_shipdate`.
- Do not use supplier path.
- Aggregates may answer via `customer_region` when grain fits.

Canonical fact SQL:

```sql
SELECT
  customer_region.r_name AS customer_region,
  SUM(l.l_extendedprice * (1 - l.l_discount)) AS revenue
FROM lineitem AS l
JOIN orders AS o ON l.l_orderkey = o.o_orderkey
JOIN customer AS c ON o.o_custkey = c.c_custkey
JOIN nation AS customer_nation ON c.c_nationkey = customer_nation.n_nationkey
JOIN region AS customer_region ON customer_nation.n_regionkey = customer_region.r_regionkey
WHERE o.o_orderstatus <> 'C'
GROUP BY customer_region.r_name;
```

## Revenue by Supplier Region

- Metric: revenue grouped by supplier `region.r_name AS supplier_region`.
- Join path: `lineitem l` -> `supplier s` -> `nation supplier_nation` -> `region supplier_region`
- For order-date filters or cancelled-order exclusion, also join `orders o ON l.l_orderkey = o.o_orderkey`.
- Do not use customer path.
- Aggregates do not include supplier geography; use facts.
- Customer-region and supplier-region revenue can differ; both are valid for different questions.

Canonical SQL:

```sql
SELECT
  supplier_region.r_name AS supplier_region,
  SUM(l.l_extendedprice * (1 - l.l_discount)) AS revenue
FROM lineitem AS l
JOIN supplier AS s ON l.l_suppkey = s.s_suppkey
JOIN nation AS supplier_nation ON s.s_nationkey = supplier_nation.n_nationkey
JOIN region AS supplier_region ON supplier_nation.n_regionkey = supplier_region.r_regionkey
GROUP BY supplier_region.r_name;
```
