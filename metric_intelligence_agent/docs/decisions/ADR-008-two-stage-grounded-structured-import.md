# ADR-008: Use Two-Stage Grounded Structured Import

**Status:** Accepted
**Date:** 2026-08-20

## Context

Structured metric sources must be converted into the project's context representation without importing examples or model training knowledge as if they came from the source. Earlier single-stage prompting with demo templates caused unrelated demo metrics to be reproduced.

## Decision

Import structured metric sources in two stages: first extract only explicit source facts into a strict structured representation, then format that representation for the context layer. Do not include demo metric examples in extraction. Validate referenced tables and columns against the discovered schema and prevent ungrounded content from being saved. Imported table metadata may not override analyst-authoritative layers, and join paths remain owned by metric definitions.

## Rationale

Separating extraction from formatting reduces template-induced hallucination and creates a structured boundary where grounding can be checked before presentation or persistence.

## Alternatives Considered

- **Single-stage extraction and formatting with a demo template.** Rejected because it caused demo metrics to be reproduced instead of extracting only supplied content.
- **Prompt-only grounding without schema checks.** Not selected as the accepted boundary requires table-and-column validation and enforced save eligibility.

## Consequences

- Extraction must return an explicit empty metric collection when the source contains no metrics rather than inventing content.
- Formatting cannot add or alter extracted facts.
- Schema grounding occurs before persistence and must cover both tables and columns.
- The import path must preserve analyst layer and metric join-path authority.
- Table-and-column grounding and enforced save eligibility are the accepted architectural contract. Current implementation gaps against that contract are tracked in `PROJECT_STATE.md`; this ADR does not claim that the present code already satisfies it.

## Revisit When

Revisit if deterministic parsers or governed source APIs can provide equivalent structured facts and grounding without model extraction, or if evidence shows the two-stage boundary is insufficient.
