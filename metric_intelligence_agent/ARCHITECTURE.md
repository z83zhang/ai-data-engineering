# Architecture

## System Overview

Metric Intelligence Agent is a context-grounded analytics system that turns a plain-English metric question into an executed, validated result and a user-facing explanation. Its accepted architecture combines:

- an active read-only analytical data source,
- analyst-reviewed schema, table/layer metadata, and canonical metric definitions,
- a stateful execution graph for generation, execution, validation, correction, and explanation,
- a UI layer that owns presentation, caching, source selection, and feedback,
- and a separate evaluation boundary that records run quality and cost.

The normal path is: interpret the question with the active context, generate SQL, execute it, validate the result deterministically and semantically, correct failures within a bounded attempt budget, explain a verified result, log the run, and render it. Presentation is not part of the execution graph.

## Major Components

### Query and setup experience

The user-facing layer accepts plain-English questions, supplies recent user-question context, checks the session cache, renders results and metadata, and captures ratings. It receives terminal execution state rather than a preformatted answer, so it owns display decisions and does not own query correctness.

The setup experience owns connection selection, schema discovery, analyst table classification, catalog review, and trusted metric-context ingestion. Machine-readable metric facts from supported SQL and dbt manifest v10-v11 inputs are parsed deterministically; flexible structured formats may use grounded LLM extraction. The Data Source setup path alone writes the table catalog. Metric import does not grant the model authority to invent business meaning, joins, or table layers.

### Connector boundary

Connectors own source-specific connection, connection testing, schema discovery, and conversion of discovered metadata into the schema representation consumed by the context layer. Their output is a live read-only query connection plus schema metadata. They do not define metrics, business rules, join paths, or warehouse layers.

The current connector interface is implemented for DuckDB and SQLite files. Discovery is connector-specific: there is no accepted generic try-one-strategy-then-fallback pipeline in the current architecture.

### Context boundary

The context layer provides three distinct forms of knowledge:

- schema structure,
- table grain and analyst-authoritative layer metadata,
- canonical metric definitions, including formulas, exclusions, caveats, ambiguity rules, and join paths.

These inputs are assembled into the full context used by question interpretation, SQL generation, correction, semantic review, and explanation. Context loading is deterministic and makes no LLM calls. Metric definitions are authoritative for metric behavior and are the sole authority for join paths. Table metadata supports source and layer selection but must not compete with metric definitions on joins.

### Execution graph

The execution graph owns per-question orchestration. It carries a flat state containing the question, recent question context, generated SQL, execution outcome, result data, validation outcome, retry count, explanation, and token totals. The active connection and assembled context are runtime dependencies captured outside that per-question state.

The graph owns routing among generation, execution, correction, validation, and explanation. It terminates with raw state for success, unsupported/out-of-range handling, or failure. It does not own UI formatting, durable run logging, feedback, or cross-question persistence. The current architecture has no checkpointing or pause/resume boundary.

### Query and validation services

The query service uses the active context to generate SQL, execute it, and rewrite failed or semantically rejected SQL. The validation service applies deterministic result checks before an LLM semantic review. The explanation service receives only a result that passed validation and creates audience-appropriate prose.

These services do not own source switching, caching, presentation, or evaluation storage.

### Evaluation and observability

The runner initializes graph state, executes one question to a terminal state, calculates any missing terminal cost metadata, and records the run. The evaluation store owns run identity, final SQL, attempts, technical and semantic outcomes, latency, token use, estimated cost, detected layer, and human feedback. The evaluation suite uses the same runner as interactive questions and assesses known failure modes rather than serving as application-code test coverage.

## End-to-End Flow

### Normal query flow

1. The UI validates that the input is a usable plain-English question and collects the two most recent user questions as conversational context.
2. The session cache is checked within the active data-source boundary. A valid cache hit may bypass graph execution and adds no incremental model cost.
3. On a cache miss, the runner creates a fresh flat execution state and invokes the graph.
4. The generation stage interprets the question and recent question context using the complete active schema, table metadata, and canonical metric definitions. It returns raw SQL or an established unsupported/out-of-range signal.
5. SQL is executed against the active read-only connection.
6. A successful execution receives deterministic result checks. Results that pass those checks receive an LLM semantic review against the question, SQL, result sample, and active context.
7. A semantically valid result is passed to the explanation stage. The graph records terminal token and cost metadata and returns raw result state.
8. The runner logs the terminal run. The UI renders verification status, data, layer, attempts, cost, explanation, and generated SQL.
9. User feedback updates the logged run associated with that answer.

### Failure and retry flow

- A SQL execution error routes to correction when attempts remain.
- A deterministic or semantic validation failure supplies its reason to the same correction path.
- Corrected SQL is executed and validated again.
- Exhausting the bounded attempt budget ends with an explicit failure and the last attempted SQL; an unverified result is not presented as successful.
- For the bundled demo, a recognized temporal request outside its known range terminates before SQL execution with an explanatory message. Custom sources have no single global date-range contract and therefore do not use this early-stop rule.

### Custom-source setup and switching flow

The current repository can connect a supported custom source, discover schema, persist analyst layer classifications, generate and review a catalog, and import metric definitions from pipeline/dashboard SQL, supported dbt manifests, and flexible structured files. Manual metric entry, setup validation, activation, and previous-session reload are not yet complete.

The accepted activation boundary is nevertheless settled: all required custom context must exist, the analyst explicitly activates it, and the custom connection, assembled context, graph, and active-source identity are replaced together. The custom connection becomes query-active immediately. Switching in either direction clears conversation history, query cache, rating-submission state, the previous run identity and result, and any pending example question. The evaluation connection and history persist.

## Data and Context Architecture

The bundled demo and custom sources have isolated context. Bundled context is read-only to the setup experience and remains available independently of custom setup. Custom context is stored separately and is not active merely because files exist.

The architectural authority order is:

1. Canonical metric definitions govern formulas, exclusions, caveats, ambiguity handling, and join paths.
2. Analyst layer classifications govern table-layer identity and remain authoritative after imports.
3. Table metadata describes grain, columns, layer-selection guidance, and source suitability without defining joins.
4. Schema metadata describes available database structures but is not a source of inferred business meaning.

The runtime context is an assembled structured text representation of these sources rather than a retrieval index or semantic graph. The execution graph is a workflow/state representation, not a business-knowledge graph. Retrieval-based context selection is deferred.

The active data source is a coherent unit: connection, assembled context, compiled execution graph, source identity, and any source-specific range metadata must change together.

## LLM Responsibilities and Boundaries

The LLM is responsible for:

- interpreting a plain-English question with recent question context,
- generating SQL from the active context,
- rewriting SQL after technical or semantic failure,
- semantically reviewing an executed result,
- explaining a verified result,
- transforming discovered schema metadata where a connector requires it,
- generating and refining catalog prose from schema plus analyst classifications,
- and extracting explicit metric content from flexible structured sources where no deterministic parser is supported.

The LLM is not allowed to independently:

- alter analyst-authoritative table layers,
- infer join paths from column names or schema resemblance,
- add metrics, tables, columns, business rules, or joins absent from trusted inputs,
- override canonical metric definitions,
- or present an unvalidated result as verified.

SQL generation, semantic review, explanation, ambiguity interpretation, and flexible-source extraction remain model-generated behavior even at deterministic temperature settings. System-enforced behavior includes deterministic parsing of supported SQL and dbt artifacts, context-file loading, query execution, deterministic result checks, attempt limits, routing, source-scoped session state, and run logging. Definition and assumption disclosure is currently best-effort explanation text; structured guaranteed disclosure is deferred.

Flexible structured import uses separate extraction and presentation-formatting stages. All metric-import paths ground referenced tables and columns against discovered schema and require explicit acknowledgment before unresolved references may be saved. Metric import writes metric definitions only; table-catalog persistence belongs exclusively to Data Source setup.

## Validation and Trust Boundaries

The architecture prevents plausible output from becoming a trusted answer through layered checks:

- Database execution establishes whether generated SQL is technically executable.
- Deterministic checks reject empty results and all-null numeric outputs and surface negative numeric values for review.
- An LLM semantic review assesses whether the result answers the question and follows the active metric and layer context.
- Technical and semantic failures enter one bounded correction loop.
- Exhausted retries terminate as failure.
- The demo's range contract can terminate unsupported temporal questions before execution.

Execution outcomes and deterministic checks are system-enforced. Semantic correctness, range recognition, ambiguity interpretation, and explanations are model judgments constrained by context. They are not deterministic guarantees. A semantic-review response that does not conform to its required decision format must not silently become a successful validation.

## State, Caching, and Source Switching

Per-question execution state is flat and ephemeral. Conversation messages, recent results, cache entries, and feedback UI state are session-local. Evaluation history is persistent local state and is intentionally independent of source switching.

Cache identity must isolate data sources and preserve conversational meaning. A context-dependent follow-up must not reuse an answer generated without that context. Each answered interaction must receive distinct run and feedback attribution, including when result computation is reused.

Source switching is atomic at the architectural level: connection, context, graph, and source identity move together. It clears question-specific state but retains the evaluation connection and history. Setup working state is not part of the required clearing boundary.

## External / Storage Boundaries

- Analytical database access is read-only. Current custom sources are DuckDB and SQLite files; additional database integrations are deferred.
- The system retains live connection objects for the session and does not persist database credentials.
- Bundled demo context is application-owned and read-only. Generated custom context, analyst classifications, and backups are local persistent artifacts isolated from the demo.
- Chat, cache, active connection, compiled graph, and setup working data are session-local.
- Evaluation runs and ratings are stored in a local persistent DuckDB database. This is not a shared multi-user observability system.
- The OpenAI API is an external runtime dependency for model-generated stages.

## Architectural Principles

- Canonical definitions take precedence over ad hoc generation and schema guessing.
- Metric definitions alone own join paths; table catalogs do not.
- Analysts own table-layer classifications.
- LLM output is bounded by trusted context, deterministic checks, and explicit routing.
- Business rules live in source-specific context rather than demo-specific generation prompts.
- Demo and custom context remain strictly isolated.
- Execution produces raw state; presentation remains outside the graph.
- One runner and logging path serve interactive and evaluation execution.
- Database-agnostic behavior covers supported dialect generation, layer reporting, monitoring, and evaluation, not only prompt wording.
- Failure is preferable to returning an unverified answer.

## Current Architectural Limitations / Deferred Production Architecture

- Custom-source manual metric entry, validation, explicit activation, and previous-session reload are deferred; connection, discovery, classification, catalog work, SQL import, and structured import exist now.
- Structured, guaranteed definition and assumption disclosure is deferred; current prose is LLM-generated.
- Connectors beyond DuckDB and SQLite are deferred.
- Retrieval-based context loading is deferred; the full static context is currently supplied to model stages.
- Production multi-user authentication, shared sessions, shared caching, and shared evaluation storage are deferred.
- Durable graph checkpointing and pause/resume are not part of the current architecture.

## Known Intent-vs-Implementation Gaps

- The accepted custom activation and reload boundary is not reachable from the current setup UI.
- Metric definitions are the accepted sole join-path authority, but bundled catalogs and catalog enrichment currently contain or write join paths.
- Custom-source layer reporting and evaluation detection are still hardcoded to demo table names.

## Open Architecture Questions

No unresolved architecture decisions remain from the reconciliation. The items above are implementation gaps or explicitly deferred capabilities, not open design questions.
