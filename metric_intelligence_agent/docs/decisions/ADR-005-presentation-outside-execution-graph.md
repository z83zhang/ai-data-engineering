# ADR-005: Keep Presentation Outside the Execution Graph

**Status:** Accepted
**Date:** 2026-08-20

## Context

The command-line and Streamlit experiences need different result formatting. Embedding terminal answer construction and failure presentation in the execution graph couples orchestration to one interface and obscures the raw outcome state.

## Decision

The execution graph terminates with raw state for success, out-of-range handling, or failure. Each consuming interface owns its presentation. The graph does not contain output-formatting or failure-formatting nodes.

## Rationale

This keeps orchestration focused on query correctness and allows multiple interfaces to render the same terminal state appropriately.

## Alternatives Considered

- **Keep output and failure formatting as graph nodes.** Rejected because it couples presentation to workflow execution and forces different interfaces through one display contract.

## Consequences

- Terminal state must expose the result, SQL, explanation, validation outcome, error, attempts, and relevant metadata needed by consumers.
- UI and command-line entry points are responsible for success, failure, and out-of-range rendering.
- Presentation changes do not require graph topology changes.

## Revisit When

Revisit only if all supported consumers adopt a shared, interface-neutral response contract that materially benefits from being produced inside the graph.
