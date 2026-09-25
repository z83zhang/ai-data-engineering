# Batch 2: runtime validation and retry correctness

Local baseline: `f671d024b5173aa716421403e5100ec229117089` (`main`), containing
Batch 1 commit `30b4571`. Baseline checks: 307 tests plus 2 subtests; live 10/13.
Work was performed against local main. Batch 2 is left uncommitted for review;
no commit, merge, push, or remote checkout was performed in this task.
The unrelated `batch1.diff`, `batch1fix.diff`, and history-backup bundle were
preserved without staging, modifying, or deleting them.

## Files changed

- `agent.py`: pure semantic-decision parser; reflection returns SQL and usage only.
- `graph.py`: sole execution ownership, bounded execution attempts, typed cost,
  and one shared terminal accounting node.
- `eval/runner.py`: initial execution count zero; documented missing-cost fallback
  runs only if graph cost is absent, without competing with graph-owned cost.
- `eval/run_eval.py`: print existing result/verification/layer fields and terminal
  attempt per case; scoring and case execution are unchanged.
- `eval/fixtures/provenance.json`: refresh only the whole-file `agent.py` source
  hash and add a review note. The hash necessarily changes for runtime edits.
  AST comparison confirmed demo generation, date discovery, context loading,
  generation/explanation functions, and SQL-system-message construction unchanged.
  Reference checks still pass; no goldens, queries, tolerances, or contracts changed.
- `tests/test_agent_validation.py`: strict-parser, usage-retention,
  unchanged deterministic rejection, and execution-free reflection tests.
- `tests/test_graph_retry.py`: real graph/runner/service integration with scripted
  model responses and a connection spy, covering execution counts and accounting.
- `PROJECT_STATE.md`: closed reliability gaps and preserved empty-result policy.
- `eval/BATCH2_VERIFICATION.md`: this review/verification record.

## Runtime contract

Only the whole positive response `VALID: yes` grants semantic validity. The
negative form is exactly `VALID: no` followed by a nonempty `REASON: ...` line.
Case, surrounding whitespace, spaces/tabs around the colon, and blank lines are
normalized. Prose, missing/non-string content, embedded positive text, conflicting
decisions, incomplete negative forms, and extra lines fail with a deterministic
invalid/unrecognized-validator reason. Token usage is retained even for rejected
responses. Malformed output follows the same bounded correction path as other
validation failures; exhaustion returns unverified state and no explanation.

`reflect_sql` no longer accepts a connection or attempt budget and never calls
`run_sql`. The graph owns budget/routing and is the only caller that executes SQL
for a question. Attempt 1 is the initial SQL execution; each correction advances
the attempt once before its SQL is executed. A limit of 3 permits at most 3 total
executions. Out-of-range termination has attempt 0 and no execution. A budget
below 1 fails before model/database work. Reflection clears prior verification
and result data so those cannot authorize corrected SQL before execution/review.

Each completed model stage adds its own input/output usage to state. All graph
terminal paths converge on `finalize`, which uses final totals exactly once:
`cost_usd = utils.compute_cost(total_input_tokens, total_output_tokens)`.
`cost_usd` is now declared in `AgentState`. The runner's architecture-documented
fallback remains for a graph omitting cost; production graph tests assert it is
never called. No independent pricing formula or accounting subsystem was added.

## Deterministic evidence

The graph tests execute the real `generate_sql`, `reflect_sql`, `run_sql`, and
`validate_result` functions, replacing external model/database I/O only. They
assert the ordered model/execution events, one validation invocation per successful
execution, exact SQL sequence, final attempt, token totals, cost, logging state,
and explanation presence/absence. A spy confirms graph cost is calculated once
and runner fallback never runs for production terminal states.

Scripted input/output usage per call: generation 10/1, reflection 20/2,
semantic review 30/3, explanation 40/4.

| Scenario | Executions / final attempt | Input/output tokens | Explained |
|---|---:|---:|---|
| First-attempt success | 1 / 1 | 80 / 8 | yes |
| Technical failure then success | 2 / 2 | 100 / 10 | yes |
| Semantic rejection then success | 2 / 2 | 130 / 13 | yes |
| Malformed review then success | 2 / 2 | 130 / 13 | yes |
| Two technical corrections then success | 3 / 3 | 120 / 12 | yes |
| Two semantic corrections then success | 3 / 3 | 180 / 18 | yes |
| Three technical failures | 3 / 3 | 50 / 5 | no |
| Three semantic failures | 3 / 3 | 140 / 14 | no |
| Three malformed reviews | 3 / 3 | 140 / 14 | no |
| Semantic, technical, malformed-final failures | 3 / 3 | 110 / 11 | no |
| Two semantic failures, technical-final failure | 3 / 3 | 110 / 11 | no |
| Three empty results | 3 / 3 | 50 / 5 | no |
| Out-of-range | 0 / 0 | 10 / 1 | no |

Each row asserts exact `compute_cost` equality, not merely a nonzero cost.
Additional tests exercise a one-execution budget with technical, semantic, and
malformed-review failure, and a direct reflection call with an execution spy.

| Command | Actual result |
|---|---|
| `python -m pytest tests/test_agent_validation.py -q` | 30 passed; 0 failed, 0 skipped. |
| `python -m pytest tests/test_graph_retry.py -q` | 17 passed; 0 failed, 0 skipped. |
| `python -m pytest tests -q --disable-warnings --tb=short` | 354 passed and 2 subtests passed; 0 failed, 0 skipped. Run before and after adding live per-case diagnostics; both passed. |
| `python -m py_compile agent.py graph.py eval/runner.py tests/test_agent_validation.py tests/test_graph_retry.py` | Passed. |
| `python -m py_compile agent.py graph.py eval/runner.py eval/run_eval.py tests/test_agent_validation.py tests/test_graph_retry.py` | Passed for all changed Python files. |
| `python -m eval.golden --check` | 10 base-fact case checks, 8 distinct aggregate cross-checks, and provenance/fixture freshness passed. |
| `git diff --check` | Passed. |

Tests ran outside the Windows sandbox for normal temporary-directory access.
All 307 existing tests remain green; no existing test was relaxed or modified.

## Live TPC-H measurement

Live command: `$env:PYTHONUNBUFFERED='1'; python -m eval.run_eval`.
Model remains `gpt-4o`, temperature 0. Batch 1 scoring/goldens remain unchanged.
**Result: 10/13 (76.9%), unchanged from Batch 1; 3 failures, 0 skips.**

| Case | Result correctness | Terminal verification | Layer match | Overall | Attempt |
|---|---|---|---|---|---:|
| What was daily revenue in January 1995? | pass | pass | pass | PASS | 1 |
| What was revenue by month in 1995? | pass | pass | pass | PASS | 1 |
| What was total revenue by year? | pass | pass | pass | PASS | 1 |
| What is total revenue by customer region? | pass | pass | fail | FAIL | 1 |
| What is total revenue by customer nation? | pass | pass | fail | FAIL | 1 |
| What is total revenue by supplier region? | pass | pass | pass | PASS | 1 |
| What is total revenue by region? | pass | pass | fail | FAIL | 1 |
| What is total revenue? | pass | pass | pass | PASS | 1 |
| What is order volume by month in 1995? | pass | pass | pass | PASS | 1 |
| What is average order value by month in 1995? | pass | pass | pass | PASS | 1 |
| What was revenue last year? | N/A | N/A | N/A | PASS | 0 |
| What is revenue this month? | N/A | N/A | N/A | PASS | 0 |
| What was revenue in 2024? | N/A | N/A | N/A | PASS | 0 |

Out-of-range cases pass their separate early-termination contract; execution and
semantic verification are intentionally not applicable (raw success/valid remain
false). They execute no SQL. All ten answerable results independently match the
goldens and have positive runtime verification. The three failures still use facts
where the unchanged evaluator requires aggregates.

Reported average attempts: **0.77** (10 actual executions across 13 questions);
estimated cost: **$0.3112**. The average now includes zero for early termination,
rather than the prior initial placeholder of one. No case in this sample needed
correction; deterministic integration tests provide the retry/failure evidence.
The live process completed successfully (exit code 0) and recorded all cases.

## Boundaries and remaining limits

Zero-row/all-null deterministic rejection, Batch 1 scoring contracts, layer and
alias rules, numeric tolerances, UI, caching, source switching, custom connectors/
context/import behavior, model selection, dependencies, and evaluation-store schema
are unchanged. Empty answers still require a separate product/architecture decision.
No jaffle_shop data or evaluation was used. The stricter shared runtime trust and
accounting rules naturally also apply when using a custom connection.

No manual Streamlit test is required for this scoped runtime change, and none is
claimed. Existing automated UI tests ran in the full suite. Model/network exceptions
outside completed terminal runs retain existing handling; this batch adds no new
transport retry policy. Deterministic tests establish routing/accounting behavior;
one live run is a measured sample, not a guarantee of future model decisions.
