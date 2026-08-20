# ADR-002: Metric Definitions Own Join Paths

**Status:** Accepted
**Date:** 2026-08-20

## Context

The same tables can participate in different valid business joins depending on the metric, such as customer versus supplier geography. Schema shape and naming conventions do not establish which path represents the intended business meaning. Allowing multiple context artifacts to define joins also creates conflicting authority.

## Decision

Canonical metric definitions are the sole authority for join paths. Table catalogs may describe grain, columns, layers, and source suitability, but must not define or infer join paths.

## Rationale

Join selection is metric-specific business logic. Keeping it with the metric prevents schema guesses and avoids contradictory join guidance across context sources.

## Alternatives Considered

- **Infer joins from column names or schema conventions.** Rejected as unreliable and insufficiently grounded.
- **Store verified joins in both table catalogs and metric definitions.** Rejected because it creates competing sources of truth and obscures metric-specific meaning.

## Consequences

- Every metric that requires joins must provide an explicit trusted path.
- Catalog generation and enrichment must not introduce join authority.
- Missing join evidence must remain unresolved rather than being filled from naming conventions.

## Revisit When

Revisit only if the project adopts a governed semantic system that can represent metric-specific joins with equivalent or stronger authority and conflict resolution.
