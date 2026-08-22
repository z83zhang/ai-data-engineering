# ADR-009: Deterministic Parsing for Structured Facts

**Status:** Accepted
**Date:** 2026-08-21

## Context

Grounding checks reduce the risk of saving hallucinated metric context, but they do not justify re-deriving facts with an LLM when an input already has a stable, machine-readable structure. Pasted SQL, dbt model SQL, and the established dbt manifest v10–v11 semantic shape expose factual tables, columns, formulas, joins, and filters directly.

Metric imports also previously produced table-catalog updates, creating competing ownership with the Data Source setup path and risking changes to analyst-owned metadata.

## Decision

Parse factual metric information deterministically wherever the input has a supported machine-readable structure:

- parse pasted SQL and dbt model SQL with `sqlglot`;
- read dbt manifest model fields directly;
- read dbt metrics and semantic models only from the supported manifest v10–v11 shape.

Reserve the LLM for business-facing descriptions and business-rule/grain prose on deterministic paths, and for grounded extraction of genuinely unsupported or flexible structured formats through the “Other structured format” path.

The Data Source setup path exclusively owns and writes the table catalog. Metric-definition imports may display source-provided table observations for analyst information but must never write to `table_catalog.md`.

## Rationale

Deterministic parsing preserves explicit source facts and removes avoidable model interpretation from the trust boundary. Version-scoping dbt semantics avoids silently guessing at incompatible artifact shapes. Exclusive table-catalog ownership prevents metric imports from changing analyst-governed table metadata.

## Alternatives Considered

- **Use LLM extraction for every input.** Rejected because it discards reliable structure and exposes factual fields to avoidable model misreading.
- **Build dedicated parsers for every named format.** Rejected because there is no usage evidence justifying special LookML or Cube parser investment.
- **Claim universal dbt metrics compatibility.** Rejected in favor of an explicit v10–v11 contract and graceful fallback for unrecognized shapes.

## Consequences

- SQL and supported dbt paths derive tables, columns, formulas, filters, joins, and grouping facts without LLM inference.
- dbt metric support is intentionally limited to the manifest v10–v11 `semantic_models` plus `metrics` shape; pre-1.6, v1.12+ flattened/Ossie, and Fusion/v2.0 artifacts remain unsupported.
- LookML, Cube, and other structured formats remain on the flexible LLM extraction path with schema grounding and acknowledgment-gated saving.
- Metric imports cannot persist table-catalog updates.
- The LLM remains responsible for prose and judgment, not machine-readable factual extraction on supported deterministic paths.

## Revisit When

Revisit when usage evidence shows that another structured format or dbt artifact shape is common enough to justify a governed deterministic parser, or when `sqlglot` cannot represent required production SQL faithfully.
