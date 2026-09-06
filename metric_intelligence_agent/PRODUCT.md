# Product

## Problem

Teams with mature data stacks often have top-line metrics in dashboards but still rely on analytics engineers to answer derived metric questions. Correct answers require knowledge of the warehouse, canonical metric definitions, trusted data layers, and business rules that is scattered across documentation, messages, and individual expertise.

The product is intended to make that knowledge usable by non-technical question askers without requiring them to write SQL or wait for an analytics engineer.

## Target Users

- **Primary users: product managers and data scientists.** They ask metric questions in plain English and use the returned results in day-to-day analysis and decisions.
- **Important secondary users: analytics engineers.** They configure a data source, provide and review trusted metric context, classify warehouse tables, and validate the setup for their team.

## Core Product Promise

The product answers derived metric questions using analytics-team-approved definitions and trusted warehouse context, checks its work before returning a result, and gives users enough explanation to understand and assess the answer.

It is a metric-intelligence product, not generic text-to-SQL: the value is the combination of company-specific meaning, verified results, and a usable plain-English experience.

## Core User Workflow

1. A product manager or data scientist asks a metric question in plain English against the active source. Recent questions may provide context for a follow-up.
2. The product interprets the question using the active source's approved metric definitions and warehouse context, obtains a result, and verifies both its technical execution and semantic correctness. If a check identifies a correctable problem, the product retries within its allowed limits.
3. The user receives the verified result with a plain-English explanation plus the data layer and cost metadata. For custom sources, structured metric facts and provenance-tracked notes are available to the explanation stage, but the explanation itself remains free-form LLM prose, so definition and assumption disclosure in the answer is not guaranteed. Unsupported or unresolved questions produce a clear limitation or failure instead of an unverified answer.
4. The user can rate the answer so its quality can be monitored.

For custom sources, the current product can connect supported databases, discover schema, collect and persist analyst layer classifications, generate and review a catalog, import layered metric definitions from pipeline/dashboard SQL, dbt manifests, and other structured formats, and let analysts review parsed facts, annotate metrics, and correct relationships. Blank-form manual authoring of raw metric facts is intentionally not part of the product. Setup validation, explicit activation, and previous-session reload remain deferred. The intended activation behavior is already settled: once those capabilities are completed and the required context exists, an analyst explicitly activates the source; it becomes query-active immediately, question-specific conversation, cache, feedback, and prior-result state are cleared on either source switch, and evaluation history is retained.

## Product Principles

- **Correctness over plausible output.** A fluent answer is not sufficient; results must be checked before they are presented as verified.
- **Canonical business meaning.** Approved metric definitions and their business rules take precedence over ad hoc interpretation.
- **Grounded configuration.** Setup content must come from analyst decisions or explicit trusted source material, not guesses based on naming conventions or model training data.
- **Analyst authority.** Analyst table-layer classifications remain authoritative, including when additional structured sources are imported.
- **Explainable answers.** Users currently see the data layer and cost metadata. Structured custom-source metric facts and notes feed the runtime explanation context, but the explanation remains LLM-generated and may not disclose every applicable definition or assumption. Guaranteed structured disclosure remains the intended future behavior.
- **Usability for non-SQL users.** Asking and understanding a metric question should not require writing SQL.
- **Database-agnostic product behavior.** Company-specific business rules belong in the configured context, and the product intent extends across supported database dialects, layer reporting, monitoring, and evaluation rather than being limited to a single demo schema.
- **Source isolation.** Bundled demo context and custom company context remain separate, and switching sources must not carry question-specific state from one source into another.

## Trust and Correctness Requirements

### Currently required behavior

- Use canonical metric definitions exactly, including formulas, exclusions, caveats, and other documented business rules.
- Treat metric definitions as the sole source of truth for join paths. Do not infer join paths from column names or place competing join-path authority in table catalogs.
- Treat analyst-selected table layers as authoritative. Structured imports must not override those selections.
- Extract only information explicitly present in supplied source material. Do not invent absent metrics, tables, joins, or business rules.
- Validate tables and columns referenced by structured metric imports, and require explicit acknowledgment before unresolved references may be saved.
- Validate a result with deterministic result checks and a semantic review before presenting it as verified.
- Attempt to correct technical or semantic failures only within a bounded retry process. If the product still cannot verify an answer, report failure rather than presenting it as trusted.
- Apply documented ambiguity rules. When a supported default applies, disclose the assumption; when the question's context makes that default unsafe, ask for clarification.
- Clearly report unsupported or out-of-range requests where the active source has an established range rule. The bundled demo must stop recognized out-of-range temporal requests before querying. A single global date-range rule is not required for custom databases.
- Surface the queried data layer and cost metadata. For custom sources, make structured metric facts and provenance-tracked notes available to the runtime explanation context; the LLM-generated explanation may include the definition used and assumptions made, but that disclosure is not currently guaranteed.
- Keep cached answers scoped to the active data source. Context-dependent follow-ups must not receive a cached answer that ignores their conversational meaning, and each answered interaction must have distinct logging and feedback attribution.

### Explicitly deferred production work

- Make definition and assumption disclosure structured and guaranteed rather than relying on best-effort prose.
- Complete the custom-source workflow for setup validation, explicit activation, and previous-session reload. This extends the existing connection, schema discovery, catalog, classification, metric-import, and review/annotation capabilities.
- Add further database integrations beyond the currently supported sources while preserving database-agnostic behavior.
- Add production-grade multi-user operation, authentication, shared state, and shared caching.
- Add retrieval-based context loading for larger bodies of company knowledge.

## Non-Goals

- Becoming a general-purpose text-to-SQL tool.
- Replacing a full semantic layer such as dbt Semantic Layer, Cube, or Looker LookML.
- Inferring join paths from schema column naming conventions.
- Supporting multi-user session sharing or authentication in the current scope.
- Persisting database credentials.
- Adding retrieval-augmented context loading in the current scope.

## Open Product Questions

No unresolved product-intent questions remain from the reconciliation. Remaining gaps are implementation work against the decisions above.
