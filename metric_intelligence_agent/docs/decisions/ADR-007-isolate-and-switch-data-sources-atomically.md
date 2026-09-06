# ADR-007: Isolate Demo and Custom Sources and Switch Atomically

**Status:** Accepted
**Date:** 2026-08-20

## Context

The bundled demo and analyst-prepared custom sources have different connections, context, and range behavior. Mixing their artifacts or retaining question-specific state across a switch can produce answers grounded in the wrong source. Evaluation history, however, is intended to span source changes.

## Decision

Keep bundled demo context read-only and separate from generated custom context. Activating a source replaces its connection, assembled context, execution graph, and source identity as one operation. Switching in either direction clears conversation history, query cache, rating-submission state, prior run identity and result, and pending example input, while retaining the evaluation connection and history. Custom activation requires all required context and an explicit analyst action.

## Rationale

Strict isolation protects the always-available demo and prevents stale question state from crossing source boundaries. Atomic switching keeps the active runtime resources coherent, while persistent evaluation history preserves quality monitoring across sources.

## Alternatives Considered

- **Overwrite bundled context with custom files.** Rejected because it risks corrupting the demo and mixing source knowledge.
- **Use one context directory with a flag.** Rejected in favor of physically separate context artifacts.
- **Activate custom context merely because files exist.** Not selected; activation is an explicit analyst action.

## Consequences

- A custom source is not query-active until its connection and required context are ready and explicitly activated.
- Source switches invalidate question-specific cache and interaction state.
- Setup working state need not be cleared, and evaluation history must survive switches.
- Demo-only range rules are not applied to custom sources.

## Revisit When

Revisit if multi-source queries or concurrent active sources become an accepted product requirement and a stronger isolation model is defined.
