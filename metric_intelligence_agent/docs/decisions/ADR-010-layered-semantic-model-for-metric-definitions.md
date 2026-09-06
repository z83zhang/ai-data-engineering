# ADR-010: Layered Semantic Model for Metric Definitions

**Status:** Accepted
**Supersedes:** Originally scoped C4 (manual metric entry as a from-scratch form)
**Related:** ADR-009 (deterministic SQL/dbt parsing, C3)

## Context

The current metric-definition format (`metric_import.py` / `render_metric_markdown`) represents each metric as one flat section: formula, source tables, column references, join conditions, filters, grain, business rules, trust level.

This format cannot correctly represent multi-stage SQL. Confirmed by direct testing against a two-CTE query: table/join extraction flattens CTE-internal and outer-query scope indiscriminately (`find_all` has no scope awareness), a column qualified by a CTE alias gets recorded as if the CTE name were a real table (producing false-positive grounding failures), and single-match extraction (`.find()`) for filters/grain can silently pick up a CTE-internal clause instead of the top-level one.

This is not only a parser bug. Even hand-typed entry (the originally planned C4 manual-entry form) hits the same ceiling — one join list and one filter list cannot express staged logic regardless of who or what produces the content.

Comparison against industry-standard semantic layer designs (dbt Semantic Layer / MetricFlow, Wren AI MDL, Cube) shows none use a flat per-metric record. All use a layered graph: named entities (table- or query-backed), relationships declared once via typed keys rather than per-metric join text, declared dimensions, measures, and metrics composed from measures. Notably, the already-accepted dbt-manifest import path (C3) natively produces this layered shape and is currently down-converted to fit the weaker flat format — the richest input source is losing information on the way in.

## Decision

Extend the metric-context persisted format to a layered semantic model with four persisted citizen types — **entities**, **dimensions**, **measures**, **metrics** — plus a structurally separate **notes** layer for analyst-owned business prose. **Relationships are not a fifth persisted citizen.** A relationship is a foreign-key declaration on an entity (`Entity.keys` with `type: foreign` and `references_entity`); it is derived/materialized from entity data at read time, not stored as an independent list. Full field contract, including the relationship identity/conflict rule, is in the Schema Contract section below.

The persisted format is YAML, not Markdown. `metric_definitions.md` becomes `metric_definitions.yaml`, with top-level keys `entities`, `dimensions`, `measures`, `metrics`, `notes`. The current Markdown-with-regex-headings scheme cannot reliably represent or round-trip cross-references between entities (e.g. `references_entity`); a structured format is required for referential correctness, not just readability.

Manual entry of raw facts (typing formulas/joins from scratch) is eliminated as planned capability; this supersedes the originally scoped C4. Three ingestion paths remain, all already accepted in C3 and all now populating the layered format instead of the flat one: SQL-paste (deterministic, `sqlglot`), dbt-manifest import (deterministic), and other structured formats — LookML, Cube, and similar — via grounded LLM extraction. The "no LLM" decision in this ADR applies only to the eliminated manual-entry form; it does not affect the already-accepted structured-format extraction path. A lightweight review/annotation surface — extending C3's already-accepted "support review/editing" behavior — lets analysts attach/edit notes and view/correct/merge relationship declarations across imports. This is editing parsed output, not composing raw facts from a blank form.

## Consequences

**Positive:** Fixes the CTE/staged-query representation gap at its root. Deduplicates relationship declarations instead of repeating join logic per metric, strengthening the existing "metric definitions are sole join-path authority" principle by making that authority non-redundant. The filter-must-reference-declared-dimension rule strengthens the existing grounding/trust boundary. Structured notes close the "assumption disclosure isn't guaranteed" gap already recorded in `PROJECT_STATE.md`. The dbt import path stops losing information it already correctly extracts.

**Negative / cost:** Persisted-format change touching render, parse, all import paths, context assembly, and the C4 UI (now review/annotate, not a from-scratch form). No production custom-source content exists yet (setup/activation isn't live), so migration cost is currently low but grows if deferred.

**Neutral:** Authority order is unchanged (metric definitions still own joins/formulas; table catalog unaffected). Does not reopen the deferred retrieval-based context-loading decision — the layered model still assembles into full static context per question.

**Runtime context-assembly addendum (2026-09-06):** Runtime context integration was part of this decision's original scope but was omitted from the initial six-phase implementation plan. The follow-up Phase 7 closed that gap: custom-source context assembly now prefers `metric_definitions.yaml`, falls back to legacy `metric_definitions.md` when YAML is absent, and appends relationships derived from entity foreign keys. The bundled demo context path and content remain untouched and were verified byte-for-byte. The original Consequences statement that structured notes close the assumption-disclosure gap refers specifically to those notes reaching runtime context; the generated explanation remains free-form LLM prose, so guaranteed disclosure in the output remains open.

## Alternatives Considered

- **Flat format + optional trusted reference-SQL field** (Wren's own fallback pattern for edge cases). Rejected as primary design — doesn't fix the representational ceiling or deduplicate relationships.
- **Keep C4 as originally scoped** (blank manual-entry form). Rejected — the decomposed layered format has more fields, not fewer; harder to hand-author. Analysts already have SQL; ingest it instead of asking them to retype it as structured facts.
- **Defer and ship flat-format C4 now, revisit later.** Rejected — CTE-shaped metrics are common, not an edge case; shipping against a format about to change creates near-term rework.

## References

ADR-009 (deterministic SQL/dbt parsing, C3); CTE-parsing test performed during this design review; dbt MetricFlow documentation (entities/dimensions/measures/metrics); Wren AI MDL documentation (Models, Relationships, refSql, gold-standard question-SQL pairs).

---

## Schema Contract

### Entity

| field | type | notes |
|---|---|---|
| `name` | string, unique | case-insensitive identity, same match rule as today's metric headings |
| `source` | `{type: table, value: <qualified_table_name>}` or `{type: query, value: <sql>}` | query-backed = the CTE case |
| `keys` | list of `{column, type: primary\|foreign\|unique\|natural, references_entity?}` | `references_entity` required iff `type: foreign`; must name another entity in this document |

### Relationship (derived — not independently persisted)

Not a top-level YAML section. Computed at read time from every `Entity.keys` entry where `type: foreign`.

| field | type | notes |
|---|---|---|
| `from_entity` | string | the entity declaring the key |
| `column` | string | from that entity's `keys` list |
| `references_entity` | string | must name another entity in this document |
| `key_type` | `primary\|foreign\|unique\|natural` | copied from the declaring key |

**Identity key:** `(from_entity, column, references_entity)`.

**Conflict rule:** when a re-imported entity's `keys` list differs from the currently saved version for the same entity name — a different `references_entity` or `key_type` for the same column, or a dropped foreign key — this is not silently applied as part of the normal entity replace-on-name-match. It is surfaced in the review/annotate UI for explicit confirmation before the entity update is saved, the same pattern as the metric-rename confirmation, scoped here to relationship-level changes within an entity update.

**Direction resolution rule (addendum):** A SQL equality join (a.x = b.y) establishes column equality but not foreign-key ownership direction. Direction — which entity's key is type: foreign and which it references_entity — must never be inferred from column names, table names, or query position; this would violate the same no-join-inference boundary that governs LLM behavior generally. Resolution order: (1) a schema-declared FK constraint from connector discovery, when available, is an accepted deterministic signal; (2) otherwise, direction is left unresolved and requires explicit analyst confirmation via the acknowledgment-gate mechanism before the relationship may be saved as a foreign key. This matches the pattern used by comparable semantic-layer tools (Wren AI's joinType, LookML's relationship), both of which treat cardinality as always-declared rather than inferred from SQL.

**dbt-manifest relationship signal (addendum):** A dbt `relationships` test (or equivalent manifest-level relationship declaration) is accepted as a second deterministic direction signal, alongside a schema-declared FK constraint — both are explicit, analyst-authored declarations rather than inferred guesses, consistent with the existing no-inference rule. This signal is trusted as declared, not independently verified against the live database; if a declaration is later found incorrect, correction happens through the existing relationship-conflict-on-reimport mechanism, not a separate verification step. Where a manifest relationship's direction is genuinely absent or ambiguous, it falls back to analyst confirmation, same as an unresolved SQL join.

### Dimension

| field | type | notes |
|---|---|---|
| `name` | string, namespaced `entity.dimension` | |
| `entity` | string | must match an existing entity |
| `column` | string | underlying column/expression |
| `type` | `categorical \| time` | |
| `granularity` | string | required iff `type: time` (day/week/month/...) |

### Measure

| field | type | notes |
|---|---|---|
| `name` | string, namespaced `entity.measure` | |
| `entity` | string | |
| `expression` | string | e.g. `SUM(amount)` |
| `aggregation` | `sum\|avg\|count\|count_distinct\|min\|max\|custom` | |
| `re_aggregatable` | bool | false for `avg`/ratio-like; true for `sum`/`count` |

### Metric

| field | type | notes |
|---|---|---|
| `name` | string, unique | case-insensitive save-identity key, same rename/confirm rule already agreed for C4 |
| `type` | `simple\|ratio\|derived\|cumulative` | |
| `measure` / `numerator`+`denominator` / `expression` / `window` | per-type, one required | matches the type chosen |
| `filters` | list of `{dimension, operator, value}` | must reference a declared **dimension or entity key** — never a raw column |
| `grain` | list of dimension names, optional | default group-by grain |

**`simple` precisely defined:** exposes a referenced measure's value as-is, at that measure's own grain. No additional aggregation function may be applied on top of it. If a metric applies *any* further aggregation over a measure — including reusing the same aggregation function again — it is not `simple`; it is `derived`.

**`derived` may reference measures directly, not only other metrics.** Where a metric's computation is a further aggregation over a measure's own grain (e.g. averaging a per-entity sum across entities), the `expression` field must state that aggregation explicitly — e.g. `"AVG(customer_totals.total_amount_sum)"` — never left implicit or described only in a note or comment.

**Enforcement:** a `derived` expression that applies a further aggregation function to a referenced measure is only valid if that measure's `re_aggregatable` is `true`. Grounding must reject a derived expression that further-aggregates a `re_aggregatable: false` measure (e.g. `AVG(some_avg_measure)`), the same way it rejects an unresolved table/column reference.

### Notes (separate top-level section, keyed by metric name, analyst-owned)

`description`, `business_rules`, `caveats`, `ambiguity_rules` — each independently editable, **never overwritten by re-import**, only by explicit analyst edit.

**Notes-provenance addendum (2026-09-05):** Each metric's note-set may include `provenance: system_generated|analyst_edited`. Import-created notes are `system_generated` and may be regenerated when that metric is re-imported. Saving any note field through the analyst review surface marks the whole note-set `analyst_edited`; subsequent re-imports preserve it unchanged. Existing note-sets without a provenance marker are treated as `analyst_edited` so previously saved analyst work is never overwritten based on an origin guess. Provenance is per metric rather than per field.

### Merge Rules

- Facts (entities/dimensions/measures/metrics): replace-on-name-match, case-insensitive — same rule as today, scoped to facts only.
- Notes: separate keyspace, untouched by fact re-import.
- Rename (name changes at save): requires explicit confirmation, atomic remove-old/write-new.
- Relationship conflicts: see the conflict rule under the Relationship section above — detected as part of entity re-import, not as a separate merge step, since relationships are derived from entity keys rather than independently stored.
- Grounding: entity source, dimension columns, measure expressions, and relationship key columns all go through the existing acknowledgment-gate check.

### Worked Example

Source query (the CTE case that motivated this ADR):

```sql
WITH paid_orders AS (
    SELECT o.customer_id, o.order_id, o.amount
    FROM orders o
    JOIN payments p ON p.order_id = o.order_id AND p.status = 'settled'
    WHERE o.status = 'complete'
),
customer_totals AS (
    SELECT customer_id, SUM(amount) AS total_amount
    FROM paid_orders
    GROUP BY customer_id
)
SELECT AVG(ct.total_amount) AS avg_customer_revenue
FROM customer_totals ct
JOIN customers c ON c.id = ct.customer_id
WHERE c.region = 'US'
```

Decomposed as the literal contents of `metric_definitions.yaml`:

```yaml
entities:
  paid_orders:
    source: {type: query, value: "SELECT o.customer_id, o.order_id, o.amount FROM orders o JOIN payments p ON p.order_id = o.order_id AND p.status = 'settled' WHERE o.status = 'complete'"}
    keys:
      - {column: order_id, type: primary}
      - {column: customer_id, type: foreign, references_entity: customers}

  customer_totals:
    source: {type: query, value: "SELECT customer_id, SUM(amount) AS total_amount FROM paid_orders GROUP BY customer_id"}
    keys:
      - {column: customer_id, type: primary}

  customers:
    source: {type: table, value: customers}
    keys:
      - {column: id, type: primary}

dimensions:
  - {name: customers.region, entity: customers, column: region, type: categorical}

measures:
  - name: customer_totals.total_amount_sum
    entity: customer_totals
    expression: "SUM(amount)"
    aggregation: sum
    re_aggregatable: true

metrics:
  "Average Customer Revenue":
    type: derived
    expression: "AVG(customer_totals.total_amount_sum)"
    filters:
      - {dimension: customers.region, operator: "=", value: "US"}
    # customer_totals.total_amount_sum is re_aggregatable: true (sum),
    # so this further AVG over its per-customer grain is valid per the
    # derived-metric enforcement rule above.

notes:
  "Average Customer Revenue":
    description: "Average total paid-order revenue per customer, US region only."
    business_rules: "Only settled payments and completed orders count toward revenue."
    caveats: ""
    ambiguity_rules: ""
```
