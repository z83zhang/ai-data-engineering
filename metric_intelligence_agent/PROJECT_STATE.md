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
- **dbt-manifest metric import.** Manifest schema v10-v11 is parsed deterministically into the layered ADR-010 YAML format of entities, dimensions, measures, metrics, and separately managed notes. Relationship direction resolves from declared relationships tests or schema-declared foreign-key constraints (`to`/`to_columns` or legacy expressions); genuinely ambiguous or absent direction requires explicit analyst confirmation before saving.
- **SQL and other structured metric import.** Pipeline/dashboard SQL is parsed deterministically for factual metric content, with the LLM limited to business-facing notes. LookML, Cube, and similar structured formats use grounded LLM extraction into the layered ADR-010 YAML format of entities, dimensions, measures, metrics, and notes, normalize measures to full aggregate expressions, and promote independently resolvable queryable measures to simple metrics. Explicit source-native relationship direction is retained; unresolved direction requires analyst confirmation. All paths ground tables and columns, require acknowledgment for schema mismatches, support YAML review, and save metric definitions only.
- **Metric-definition review and annotation.** The Manual tab provides grouped review of parsed entities, dimensions, measures, metrics, derived relationships, and notes. Analysts can edit provenance-tracked metric notes, delete individual metrics with confirmation and dependency visibility, and correct saved relationships without using a blank raw-fact entry form.
- **Custom-source validation and activation.** Analysts can persist up to five source-isolated known-answer questions, run all or selected questions through the production execution graph without evaluation logging or interactive caching, inspect SQL/results/explanations, record the final human pass/fail decision, and explicitly activate a custom source after all questions pass. Context saves invalidate the readiness signal until validation is refreshed. This flow, atomic activation, and a correct custom-source Query-mode answer have been exercised live against jaffle_shop.

### Present but partially complete

- **Custom-source setup.** The UI can connect a supported source, discover schema, generate and save schema context, collect and persist analyst table-layer classifications, generate/review/refine a table catalog, enrich it from supplied documentation, validate known-answer questions, and activate the prepared source. Previous-session connection reload remains unavailable.
- **Custom source switching infrastructure.** The application has a source-switch operation that replaces the connection, context, graph, and source identity while clearing question-specific state and preserving evaluation logging. The setup UI can activate the connected custom source and switch an active custom source back to the demo. It cannot reload a custom database connection after an application restart.
- **Definition and assumption disclosure.** Custom-source runtime context assembly includes structured, provenance-tracked notes alongside layered metric facts and derived relationships. The explanation prompt can use that context, but its output remains free-form LLM prose; structured, guaranteed disclosure in the answer is not implemented.
- **Database-agnostic structure.** Business context is externalized and connectors share a common interface, but SQL wording, custom layer detection, monitoring, and evaluation are not yet fully source-agnostic.

## Current Apparent Implementation Focus

Recent repository activity completed custom-source validation and activation and extended metric review with confirmed single-metric deletion. Previous-session custom-connection reload remains the narrower unfinished setup capability. This is an observation about the current repository, not an owner-established priority or roadmap.

## Partially Implemented / Known Gaps

- Custom context and validation artifacts persist, but the application does not restore a previous custom database connection after restart; the analyst must reconnect before validation or activation.
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

- Add previous-session custom-source connection reload without persisting database credentials.
- Provide structured, guaranteed definition and assumption disclosure in the generated answer; structured custom-source notes now reach runtime context, but explanation output remains free-form.
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
- Deterministic tests cover setup import/review helpers, validation persistence and UI behavior, metric deletion, and custom-source activation state. The model-driven graph behavior and 13-case evaluation suite still require live OpenAI execution.
- The deterministic result checks exercised at runtime cover zero rows and all-null numeric columns; negative numeric values are warnings passed to semantic review rather than automatic failures.

## Recently Completed Significant Work

- Added the custom database setup foundation, read-only DuckDB and SQLite connectors, schema discovery, catalog generation/review, and persistent layer classifications.
- Added deterministic pipeline/dashboard SQL import, including separate metrics for multiple aliased aggregate expressions. Added dbt manifest v10-v11 import that populates layered ADR-010 entities, dimensions, measures, and metrics, resolves direction from declared relationship signals, and requires analyst confirmation when direction remains unresolved. Metric imports write metric definitions only; table-catalog ownership remains exclusively with Data Source setup.
- Rewired grounded LookML, Cube, and similar structured-format imports to populate layered ADR-010 YAML, normalize full aggregate expressions, promote independently resolvable measures to simple metrics, retain explicit source-declared relationship direction, gate ambiguous direction on analyst confirmation, ground table-and-column references before saving, preserve analyst-owned layers, and skip or flag metric-to-metric composition.
- Replaced the blank Manual-tab placeholder with a review/annotation surface for metric notes and derived relationships, including grouped entity/metric display with a secondary raw-YAML view.
- Added per-metric notes provenance so system-generated notes refresh on re-import while analyst-edited notes remain protected, plus a non-blocking re-import notice when protected notes are retained.
- Added a non-blocking near-match notice when an imported metric name plausibly refers to an existing metric but does not match it exactly.
- Integrated layered custom-source YAML, provenance-tracked notes, and derived relationships into runtime context assembly while preserving the bundled demo context unchanged.
- Added case-insensitive metric-heading identity across metric-definition save paths: saving replaces every existing matching-name section with exactly one new definition.
- Added distinct active-query-source and setup-connected-schema indicators so setup grounding state is visible without changing explicit activation behavior.
- Added the Streamlit chat experience with caching, recent-question context, input validation, result metadata, and feedback.
- Added the shared evaluation runner, persistent run logging, token/cost tracking, and the 13-case failure-mode evaluation suite.
- Removed graph-owned output/failure presentation so terminal and UI entry points render raw execution state independently.
- Built the Validate tab with source-isolated persisted question sets, advisory single-value checks, analyst-final pass/fail decisions, subset reruns, per-question status, readiness staleness, and activation gating. Live verification against jaffle_shop covered known-answer validation, atomic activation, and a correct Query-mode payment-total answer.
- Fixed activation's duplicate-widget failure by moving source lifecycle helpers out of the Streamlit entry point into `source_management.py`; setup pages no longer import and re-execute `app.py`.
- Grounded the jaffle_shop custom semantic context by adding the payments relationship, correcting revenue/customer-total joins, adding `avg_order_amount`, removing the unsupported customer-region rule, and deleting the ungrounded `avg_order_value_test` fixture.
- Added confirmed single-metric deletion to the Manual review tab. Deletion removes the metric, notes, and uniquely owned measure/entity facts; preserves and identifies shared dependencies; creates a backup; and invalidates validation readiness.

## Next Established Work

- Implement previous-session custom-source connection reload using the accepted source-switch boundary without persisting credentials.
- Correct context-dependent cache identity and create distinct logging/feedback attribution for cached interactions.
- Make custom-source layer detection, monitoring, and evaluation source-aware.
- Add connector-level foreign-key discovery as a separately reviewed extension; current structured-format imports use source-native relationship declarations and analyst confirmation because connector schema discovery exposes columns only.
