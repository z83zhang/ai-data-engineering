# ADR-001: Keep Business Rules in Source Context

**Status:** Accepted
**Date:** 2026-08-20

## Context

The system must answer company-specific metric questions without embedding one demo warehouse's formulas, exclusions, ambiguity rules, or table guidance in universal SQL-generation behavior. Hardcoded business rules would couple the orchestration layer to TPC-H and make new sources require code changes.

## Decision

Keep SQL-generation and correction instructions source-agnostic. Store canonical formulas, exclusions, ambiguity handling, layer guidance, and other company-specific business rules in the active source context. Canonical context overrides ad hoc interpretation.

## Rationale

This preserves one orchestration flow across supported sources and makes the quality of source-specific answers depend on analyst-reviewed context rather than demo-specific code. It also keeps business knowledge inspectable and configurable outside the execution logic.

## Alternatives Considered

- **Hardcode business rules in model prompts or application logic.** Rejected because it couples the system to one schema and requires implementation changes for each deployment.

## Consequences

- Context must be complete and internally consistent enough to guide generation, correction, validation, and explanation.
- New source-specific business behavior is introduced through reviewed context rather than universal prompt changes.
- Model compliance remains constrained rather than guaranteed; validation is still required.

## Revisit When

Revisit only if externalized context cannot express a required class of business rule, or evidence shows that a different governed representation is necessary across supported sources.
