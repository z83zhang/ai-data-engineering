# Project State

## Current Working Capabilities

### Working now

- **TPC-H demo query flow.** The application builds an in-memory demo database with daily and monthly aggregate tables, loads bundled schema and metric context, and answers plain-English metric questions through the full generation, execution, validation, correction, and explanation flow.
- **Bounded verification loop.** Executable results receive deterministic checks followed by an LLM semantic review. Technical and semantic failures can be rewritten and retried up to the configured attempt limit. Terminal failures expose the last error and SQL rather than a successful answer.
- **Demo date-range handling.** The demo context contains its known date range and can return an out-of-range response before SQL execution. This behavior is model-interpreted and applies only to the demo.
- **Interactive query UI.** The Streamlit interface supports question input validation, example questions, result tables, generated-SQL inspection, layer/attempt/cost metadata, free-form explanations, recent-question context, conversation clearing, and correct/incorrect ratings.
- **Session caching.** Results are cached per normalized question and active source. Cache hits bypass graph execution, display zero incremental cost, and reuse the stored result.
- **Evaluation logging.** Interactive and evaluation runs share one runner and are logged locally with SQL, attempt count, technical and semantic outcomes, latency, token counts, cost, detected layer, and human-review fields.
- **Context separation.** Bundled demo context and generated custom context use separate locations. Setup writes do not modify the bundled demo context.
- **Custom file-database connections.** DuckDB files open read-only. SQLite files are attached read-only through DuckDB. Both connectors test connections and discover schema through source-specific paths.
- **dbt-manifest metric import.** Manifest schema v10-v11 is parsed deterministically into the layered ADR-010 format of entities, dimensions, measures, and metrics. Relationship direction resolves from declared relationships tests or schema-declared foreign-key constraints (`to`/`to_columns` or legacy expressions); genuinely ambiguous or absent direction requires explicit analyst confirmation before saving.
- **SQL and other structured metric import.** Pipeline/dashboard SQL is parsed deterministically for factual metric content, with the LLM limited to business-facing prose. LookML, Cube, and similar structured formats use grounded LLM extraction into the layered ADR-010 format, normalize measures to full aggregate expressions, and promote independently resolvable queryable measures to simple metrics. Explicit source-native relationship direction is retained; unresolved direction requires analyst confirmation. All paths ground tables and columns, require acknowledgment for schema mismatches, support YAML review, and save metric definitions only.

### Present but partially complete

- **Custom-source setup.** The UI can connect a supported source, discover schema, generate and save schema context, collect and persist analyst table-layer classifications, generate/review/refine a table catalog, enrich it from supplied documentation, and create backups when saving.
- **Custom source switching infrastructure.** The application has a source-switch operation that replaces the connection, context, graph, and source identity while clearing question-specific state and preserving evaluation logging. The setup UI can switch from an already active custom source back to the demo, but it cannot activate or reload a custom source.
- **Definition and assumption disclosure.** The explanation prompt requests the metric definition and assumptions, but the output is free-form LLM prose and is not guaranteed or structurally represented.
- **Database-agnostic structure.** Business context is externalized and connectors share a common interface, but SQL wording, custom layer detection, monitoring, and evaluation are not yet fully source-agnostic.

## Current Apparent Implementation Focus

Recent repository activity and the remaining setup placeholders are concentrated on the custom-source setup path. Connection and schema discovery, table classification/catalog creation, and metric import exist; the unfinished portion includes manual metric entry, validation, and activation of a custom source for querying. This is an observation about the current repository, not an owner-established priority or roadmap.

## Partially Implemented / Known Gaps

- Custom context can be prepared and detected, but there is no UI control to validate, activate, or reload it as the live query source.
- Manual metric entry and the Validate tab are placeholders.
- Setup completion checks file existence rather than validating content completeness, catalog coverage, metric references, or generated schema fidelity.
- Metric definitions are the accepted sole join-path authority, but the bundled table catalog contains joins and catalog enrichment can write them.
- Generated schema SQL is LLM output constrained by prompts; it is not parsed, executed, or compared deterministically with discovered metadata before saving.
- Cache keys exclude conversation context. A follow-up can therefore reuse an answer generated under different conversational meaning.
- Cache hits reuse the original run ID, skip new evaluation logging, and direct feedback to the original record rather than giving each interaction distinct attribution.
- Layer detection is based on demo table names, so custom-source runs would generally report an unknown layer.
- An LLM semantic-review response that matches neither accepted decision format currently defaults to valid.
- Maximum-attempt cost calculation can omit tokens from the latest correction call.
- Runtime cost is terminal metadata but is not declared in the graph's typed state.
- Several older repository documents still describe removed graph presentation nodes and a formatted terminal-answer field.

## Deferred Production Work

- Complete manual metric entry, setup validation, explicit custom-source activation, and previous-session reload.
- Provide structured, guaranteed definition and assumption disclosure.
- Add database integrations beyond DuckDB and SQLite while extending dialect, layer-reporting, monitoring, and evaluation behavior accordingly.
- Add production multi-user authentication, shared sessions, shared caching, and shared evaluation storage.
- Add retrieval-based context loading for larger knowledge sets.
- Durable graph checkpointing and pause/resume are not part of the current implementation.

## Important Current Technical Constraints

- Live OpenAI access is required for model-generated query, validation, explanation, and setup stages.
- Demo startup depends on the DuckDB TPC-H extension and generates its data in memory.
- All current query execution uses DuckDB connections. SQLite is queried through a read-only DuckDB attachment.
- Custom sources do not receive a global date-range section; empty custom-source results proceed through normal validation.
- Runtime context is the complete static combination of the catalog, metric definitions, and schema. There is no retrieval or indexing layer.
- Conversation context consists of the two most recent user questions; assistant responses are not passed back to generation.
- Session state holds live connections, the compiled graph, chat/cache state, and setup drafts. There is no graph checkpointer.
- Evaluation history is a local, gitignored DuckDB file. Custom context and its backups are also local and gitignored.

## Current Validation / Test Status

- The repository contains a 13-case live evaluation suite for four failure modes: wrong data layer, wrong join path, wrong metric formula, and out-of-range handling.
- Evaluation scoring checks applicable row counts, required columns, selected numeric values, detected layers, and out-of-range state. Runs use the same graph runner and logger as interactive queries.
- The suite requires live OpenAI access and the runtime TPC-H database. No current evaluation-run artifact is tracked, so the pass rate for the present prompts and model is **[NEEDS VERIFICATION]**.
- There are no conventional deterministic unit or integration test files for the graph, connectors, setup helpers, caching, source switching, or logging.
- The deterministic result checks exercised at runtime cover zero rows and all-null numeric columns; negative numeric values are warnings passed to semantic review rather than automatic failures.

## Recently Completed Significant Work

- Added the custom database setup foundation, read-only DuckDB and SQLite connectors, schema discovery, catalog generation/review, and persistent layer classifications.
- Added deterministic pipeline/dashboard SQL import, including separate metrics for multiple aliased aggregate expressions. Added dbt manifest v10-v11 import that populates layered ADR-010 entities, dimensions, measures, and metrics, resolves direction from declared relationship signals, and requires analyst confirmation when direction remains unresolved. Metric imports write metric definitions only; table-catalog ownership remains exclusively with Data Source setup.
- Rewired grounded LookML, Cube, and similar structured-format imports to populate layered ADR-010 YAML, normalize full aggregate expressions, promote independently resolvable measures to simple metrics, retain explicit source-declared relationship direction, gate ambiguous direction on analyst confirmation, ground table-and-column references before saving, preserve analyst-owned layers, and skip or flag metric-to-metric composition.
- Added case-insensitive metric-heading identity across metric-definition save paths: saving replaces every existing matching-name section with exactly one new definition.
- Added distinct active-query-source and setup-connected-schema indicators so setup grounding state is visible without changing explicit activation behavior.
- Added the Streamlit chat experience with caching, recent-question context, input validation, result metadata, and feedback.
- Added the shared evaluation runner, persistent run logging, token/cost tracking, and the 13-case failure-mode evaluation suite.
- Removed graph-owned output/failure presentation so terminal and UI entry points render raw execution state independently.

## Next Established Work

- Implement manual metric entry.
- Implement setup validation, explicit custom-source activation, and previous-session reload using the accepted source-switch boundary.
- Correct context-dependent cache identity and create distinct logging/feedback attribution for cached interactions.
- Make custom-source layer detection, monitoring, and evaluation source-aware.
- Add connector-level foreign-key discovery as a separately reviewed extension; current structured-format imports use source-native relationship declarations and analyst confirmation because connector schema discovery exposes columns only.
