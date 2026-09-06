# ADR-003: Analysts Own Table-Layer Classification

**Status:** Accepted
**Date:** 2026-08-20

## Context

Table names and structural patterns are unreliable indicators of whether a warehouse object is a fact, dimension, aggregate, bridge, or intentionally excluded source. Incorrect classification can direct questions to the wrong grain or data layer.

## Decision

An analyst classifies custom-source tables, and those selections remain authoritative after later imports. The LLM may use the classifications to generate or refine descriptive context but may not override them.

## Rationale

The analyst who understands the warehouse is the trusted authority for table role. Treating that input as ground truth prevents model inference from silently changing source-selection behavior.

## Alternatives Considered

- **Infer layers from table names or schema structure.** Rejected because those signals are not reliable enough to establish warehouse semantics.
- **Allow structured imports to replace analyst selections.** Not selected because the reconciled owner decision preserves analyst authority.

## Consequences

- Layer classification is an explicit setup responsibility and must persist across sessions.
- Imported metadata may supplement table descriptions but cannot change the analyst-owned layer.

## Revisit When

Revisit only if a governed upstream source is explicitly designated as having equal or greater authority than analyst classification and conflict behavior is agreed by the owner.
