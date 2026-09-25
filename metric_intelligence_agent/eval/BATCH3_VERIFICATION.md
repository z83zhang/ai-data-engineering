# Batch 3 verification — 2026-09-25

Starting and final local `main` HEAD: `ea6daf71198777b7d24acaed12555c2deea76ba0`.
Changes are uncommitted for external review; nothing was pushed. New source/test/document
files are marked intent-to-add; `batch3-full.diff` includes all Batch 3 changes and excludes
the preserved Batch 1/2 diff files and history backup. The review diff itself is untracked.

## Implementation and exact files

- `model_config.py` (new), `agent.py`, `graph.py`: resolve `OPENAI_MODEL` once per graph
  construction; blank/unset defaults to `gpt-4o`. All four runtime stages receive that same
  name explicitly, with unchanged prompts and temperature. Terminal `model` captures the
  actual request model name, not a later environment lookup or a provider snapshot ID.
  Graph state contains an execution-bounded `attempt_trace` and `non_attempt_usage`.
- `eval/logger.py`: two additive nullable columns, `model VARCHAR` and `attempt_trace TEXT`.
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` makes reopening/migration idempotent.
  Existing rows and ratings are preserved; historical provenance remains null.
  New runs persist the terminal model and JSON trace; existing `layer_used` stores the
  terminal layer. No DataFrames or explanation text enter the trace.
- `layer_reporting.py` (new), `source_management.py`, `main.py`, `eval/run_eval.py`,
  `app.py`, `app_pages/query.py`, `app_pages/validate.py`: SQLGlot resolves physical
  table references through scopes. Demo metadata comes from existing layer sections in
  `context/table_catalog.md`; custom metadata comes only from the source directory's
  analyst-owned `layer_classifications.json`. Graph construction copies the mapping.
  Reporting preserves aggregated > fact > dimension precedence. Missing/ambiguous
  classifications, Bridge/Skip, unsupported sources and malformed SQL report unknown.
  A qualified name requires matching qualified metadata, except DuckDB's `main` schema
  can use the unqualified mapping. Comments, literals and CTE aliases confer no layer.
  Query display and logging consume the same terminal value. Existing UI structure and
  activation/cache behavior are unchanged; displayed layer metadata is intentionally fixed.
- `requirements.txt`: exact pins below.
- `tests/test_runtime_observability.py` (new): 30 deterministic cases covering model
  snapshots/all four stages, trace paths/accounting, JSON persistence, legacy migration,
  arbitrary source names, unknown layers, SQL spoofing and demo mappings.
- `tests/test_eval_scoring.py`: only adapt the live-runner test double to accept the
  added graph mapping argument and provide model metadata. Scorer expectations unchanged.
- `eval/fixtures/provenance.json`: mechanically refresh the `agent.py` hash and record
  why; no frozen expected results, reference SQL, or comparator contracts changed.
- `README.md`, `PROJECT_STATE.md`, `eval/BATCH3_VERIFICATION.md` (new): document the
  implementation, persistent-store migration, checks and limitations.

## Environment

Python **3.13.14**, Windows; pytest **9.1.1**. Exact direct versions were read from the
existing working environment, not selected as upgrades:

| Dependency | Pin |
|---|---|
| openai | 2.44.0 |
| duckdb | 1.5.4 |
| pandas | 3.0.3 |
| langgraph | 1.2.9 |
| streamlit | 1.58.0 |
| sqlglot | 30.17.0 |
| PyYAML | 6.0.3 |

Clean environment verification succeeded using:

```powershell
python -m venv "$env:TEMP\metric-batch3-venv-20260925"
& "$env:TEMP\metric-batch3-venv-20260925\Scripts\python.exe" -m pip install -r requirements.txt
& "$env:TEMP\metric-batch3-venv-20260925\Scripts\python.exe" -m pip check
& "$env:TEMP\metric-batch3-venv-20260925\Scripts\python.exe" -c "import openai, duckdb, pandas, langgraph, streamlit, sqlglot, yaml; import graph; print('Clean-environment imports passed')"
```

Install and imports passed; pip reported no broken requirements. The owner's working
environment was not modified. These are direct pins, not a transitive lock: clean install
resolved some different transitive versions. The full suite ran in the working environment;
the clean environment received installation, dependency-consistency and import checks.

## Trace contract

`attempt_trace` is a JSON-serializable list, at most `MAX_REFLECTION_ATTEMPTS` entries
(default 3), appended only after SQL execution. Out-of-range has `[]` and attempt 0.
Each entry has exactly these fields; this representative scripted technical correction
also demonstrates the distinction between execution failure and validation failure:

```json
[
  {
    "attempt": 1,
    "stage": "initial",
    "sql": "SELECT 1",
    "execution_success": false,
    "execution_error": "SQL execution failed",
    "row_count": null,
    "semantic_review_ran": false,
    "validation_pass": null,
    "validation_reason": null,
    "generation_usage": {"input_tokens": 10, "output_tokens": 1},
    "validation_usage": {"input_tokens": 0, "output_tokens": 0}
  },
  {
    "attempt": 2,
    "stage": "correction",
    "sql": "SELECT 2",
    "execution_success": true,
    "execution_error": null,
    "row_count": 1,
    "semantic_review_ran": true,
    "validation_pass": true,
    "validation_reason": "",
    "generation_usage": {"input_tokens": 20, "output_tokens": 2},
    "validation_usage": {"input_tokens": 30, "output_tokens": 3}
  }
]
```

`generation_usage` is initial generation or reflection, according to `stage`.
Deterministic rejection has validation_pass=false and semantic_review_ran=false;
malformed semantic responses have validation_pass=false and semantic_review_ran=true.
Explanation tokens (40 input/4 output in this example) are terminal `non_attempt_usage`,
giving final totals 100 input/10 output. Out-of-range generation also goes there.
Failed terminal runs have zero non-attempt usage. Persisted total tokens minus the sum
of trace usage recovers non-attempt usage without adding another database column.
`utils.compute_cost` remains the sole cost calculation. Trace length is execution-bounded;
SQL and reason strings are retained in full, rather than imposing a new truncation policy.

## Automated verification

| Command/check | Result |
|---|---|
| `python -m pytest tests/test_runtime_observability.py tests/test_graph_retry.py tests/test_agent_validation.py -q --disable-warnings --tb=short` | 77 passed |
| `python -m pytest tests -q --disable-warnings --tb=short` | **384 passed, 2 subtests passed**, no failures/skips (baseline 354 + 2) |
| `python -m eval.golden --check` | 10 base-fact checks + 8 distinct aggregate cross-checks passed |
| Syntax command below | Passed |
| AST comparison against starting HEAD | Demo setup/date/context functions, validation parser, SQL system-message builder and all runtime prompt strings unchanged |
| Legacy DB test | Old row/question/rating preserved; two new fields null; two repeated opens succeed |
| Live DB readback | Latest 13 rows have model=gpt-4o, valid JSON traces matching execution counts, token attribution within totals |
| Streamlit headless startup on temporary port 8519 | Health endpoint returned `ok`; process stopped afterward |

```powershell
python -m py_compile agent.py app.py app_pages/query.py app_pages/validate.py eval/logger.py eval/run_eval.py graph.py main.py source_management.py layer_reporting.py model_config.py tests/test_eval_scoring.py tests/test_runtime_observability.py
```

Frozen expected-result checksum remains
`6bc496542a91e51a58cc36ef00f1619646829ceee7be29651f3bca11bd74b08c`.
An initial full run found a stale test-double signature; a subsequent run caught a
source hash invalidated by a docstring edit. Both were corrected before the final green run.

## Live TPC-H

Ran `python -u -m eval.run_eval` (unbuffered equivalent of `python -m eval.run_eval`)
with resolved **gpt-4o**. **10/13**, unchanged from Batch 2. All ten answerable cases have
correct independent results and verified terminal states. The same three geography
cases use facts where the evaluator requires aggregates. No prompt tuning was made.
Average attempts: **0.77**; estimated cost: **$0.3107**.

| Case | Overall | Result match | Terminal verified | Layer match | Attempts | Model |
|---|---|---|---|---|---|---|
| 1 Daily revenue, Jan 1995 | Pass | true | true | true | 1 | gpt-4o |
| 2 Monthly revenue, 1995 | Pass | true | true | true | 1 | gpt-4o |
| 3 Annual revenue | Pass | true | true | true | 1 | gpt-4o |
| 4 Customer-region revenue | Fail | true | true | false | 1 | gpt-4o |
| 5 Customer-nation revenue | Fail | true | true | false | 1 | gpt-4o |
| 6 Supplier-region revenue | Pass | true | true | true | 1 | gpt-4o |
| 7 Default-region revenue | Fail | true | true | false | 1 | gpt-4o |
| 8 Total revenue | Pass | true | true | true | 1 | gpt-4o |
| 9 Monthly order volume, 1995 | Pass | true | true | true | 1 | gpt-4o |
| 10 Monthly AOV, 1995 | Pass | true | true | true | 1 | gpt-4o |
| 11 Revenue last year | Pass | n/a | n/a | n/a | 0 | gpt-4o |
| 12 Revenue this month | Pass | n/a | n/a | n/a | 0 | gpt-4o |
| 13 Revenue in 2024 | Pass | n/a | n/a | n/a | 0 | gpt-4o |

## Scope and remaining verification

Batch 1 scorer/alias/tolerance policy, Batch 2 retry semantics, zero-row rejection,
cache keys/run IDs, UI controls, source activation/reload, semantic import and jaffle_shop
behavior are unchanged. No Batch 4 work, dependency upgrades in the working environment,
new pricing system, authentication work, architecture rewrite, commit or push was included.

Model overrides must support the existing chat-completions/temperature contract; only
gpt-4o was live-tested. Cost estimates retain existing fixed gpt-4o rates for any override.
Model and source mappings refresh when a graph is constructed; no hot-reload was added.
Old cached states without layer provenance display unknown until a fresh query run.
Historical store rows cannot acquire model/trace provenance retroactively.

Browser interaction was not manually verified. Owner smoke check: run `streamlit run app.py`,
run a fresh demo aggregate query and confirm the existing layer caption; activate a prepared
custom source with persisted classifications, run a fresh query using a classified table,
and confirm the caption and logged layer agree. Existing automated Streamlit tests pass.
No PRODUCT/ARCHITECTURE/ADR changes are needed for this authorized implementation scope.
