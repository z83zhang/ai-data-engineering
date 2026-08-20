# ADR-006: Validate Before Trust with Bounded Correction

**Status:** Accepted
**Date:** 2026-08-20

## Context

Executable SQL can still answer the wrong question, use the wrong layer or formula, or return implausible results. An LLM-generated answer is therefore not trusted solely because it is syntactically valid or fluent. At the same time, unconstrained correction could loop indefinitely.

## Decision

Execute generated SQL, apply deterministic result checks, then perform a context-grounded semantic review. Route technical and semantic failures through the same bounded correction loop. Return success only after validation; when attempts are exhausted, return an explicit failure rather than an unverified answer.

## Rationale

Execution checks technical validity, deterministic checks catch objective result failures, and semantic review assesses alignment with the question and canonical context. A bounded loop allows correction while keeping cost and termination predictable.

## Alternatives Considered

- **Trust the first executable query.** Rejected because execution does not establish semantic correctness.
- **Use only deterministic checks.** Insufficient for question-to-result and business-definition alignment.
- **Retry without a fixed limit.** Rejected in favor of a bounded terminal behavior.

## Consequences

- Semantic review is a model judgment, not a deterministic proof of correctness.
- Review failures and execution failures share correction behavior.
- Unsupported or unverified questions terminate clearly instead of producing a trusted result.
- Validation response contracts must fail safely when the model does not produce a recognized decision.

## Revisit When

Revisit if measured evidence shows that the layered checks do not improve correctness, or a stronger deterministic or governed semantic-validation mechanism can replace part of the flow.
