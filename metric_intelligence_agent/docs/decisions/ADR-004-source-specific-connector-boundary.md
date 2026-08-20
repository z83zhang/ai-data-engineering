# ADR-004: Use a Source-Specific Connector Boundary

**Status:** Accepted
**Date:** 2026-08-20

## Context

Supported databases differ in connection behavior, read-only handling, schema discovery, dialect details, and driver requirements.

## Decision

Use a shared connector contract for connection, connection testing, schema discovery, and schema-context generation, with source-specific implementations behind that boundary. Database access is read-only.

## Rationale

This isolates database-specific behavior while giving setup and query orchestration a stable conceptual interface. It preserves source-specific capabilities that a lowest-common-denominator abstraction could hide.

## Alternatives Considered

- **Use one SQLAlchemy abstraction for all sources.** Rejected because it would obscure native DuckDB behavior and would not eliminate source-specific driver and read-only differences.

## Consequences

- Each supported source must implement the connector responsibilities and integrate with setup and runtime source selection.
- Source-specific discovery behavior remains encapsulated behind the connector boundary.
- The shared contract does not by itself guarantee that adding a database requires only one new file.

## Revisit When

Revisit if supported databases converge on a proven abstraction that preserves required native behavior, read-only guarantees, and schema fidelity with less source-specific code.
