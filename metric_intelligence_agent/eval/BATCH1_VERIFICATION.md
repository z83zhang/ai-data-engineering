# Batch 1 verification — 2026-09-24

Base revision: `edfb813637821c868afafb4a0044927aff1a0c5a`.

## Pre-merge corrections

The owner-requested corrections restore semantic-schema comparison: extra output
columns are accepted unless `result.exact_schema=True`; explicit aliases match
case-insensitively and ambiguous matches fail. Key aliases are declared per case
(date, year, month, region, nation); there is no fuzzy matching. Revenue/AOV now
use `abs_tol=0.005`, `rel_tol=0`, while counts remain exact. Regression controls
cover full two-decimal rounded results and errors of 0.004999, 0.005, and 0.005001,
including large scalar revenue where relative tolerance previously dominated.

Each reference query now records canonical definition sections, formula,
exclusions, grain, intended answer layer, and time semantics. Provenance includes
the normalized hash of `eval/test_suite.py`, so alias/tolerance changes invalidate
freshness. Database reference tests run automatically when the local TPC-H
extension loads and skip explicitly if unavailable. Setup/query failures after
the availability probe remain failures. Frozen numeric answers are unchanged.

| Correction verification command | Result |
|---|---|
| `python -m pytest tests/test_eval_scoring.py -q --disable-warnings --tb=short` | 184 passed, 0 failed, 0 skipped. |
| `python -m pytest tests/test_eval_golden.py -q --disable-warnings --tb=short` | 22 passed, 0 failed, 0 skipped; no opt-in environment variable. |
| `python -m pytest tests -q --disable-warnings --tb=short` | 307 passed and 2 subtests passed; 0 failed, 0 skipped. |
| `python -m eval.golden --write` followed by `python -m eval.golden --check` | Each passed 10 base-fact case checks and 8 distinct aggregate cross-checks; updated provenance hashes pass. |
| `python -m py_compile eval/scoring.py eval/golden.py eval/test_suite.py tests/test_eval_scoring.py tests/test_eval_golden.py` | All five changed Python files compiled successfully. |
| `git diff --check` | Passed. |

Correction live run (`$env:PYTHONUNBUFFERED='1'; python -m eval.run_eval`), using
**`gpt-4o`**: **10 passed, 3 failed, 0 skipped (76.9%)**, unchanged from the 10/13
baseline. Every case retained the outcome in the pre-correction final-run table
below. All ten answerable data comparisons and runtime verification flags passed;
customer-region, customer-nation, and default-region cases failed solely for
using facts instead of aggregates. All three out-of-range cases passed.
Average attempts: **1.08**; reported estimated cost: **$0.3312**. Exit code 0;
the three behavioral failures remain explicitly reported.

## Files changed

- `eval/run_eval.py`: preflight fixture validation, scorer integration, failure diagnostics, connection cleanup.
- `eval/scoring.py`: independent result comparison, terminal/out-of-range contracts, evaluator-local physical-table layer checking.
- `eval/test_suite.py`: explicit contracts and aliases for all ten answerable cases.
- `eval/golden.py`: explicit generation and freshness commands.
- `eval/fixtures/tpch_sf01.json`: nine complete result sets covering ten cases.
- `eval/fixtures/reference_queries.json`: base-fact reference SQL and eight aggregate equivalents.
- `eval/fixtures/provenance.json`: revision, normalized-text hashes, versions, scale, review status, tolerances, and limitations.
- `eval/fixtures/README.md`: contract, provenance, generation/check instructions, and limits.
- `tests/test_eval_scoring.py`: 184 offline adversarial/positive tests, including runner continuation.
- `tests/test_eval_golden.py`: 4 offline checks and 18 automatically available database checks.
- `PROJECT_STATE.md`: implemented capability and current verification status.
- `eval/BATCH1_VERIFICATION.md`: this report.

Runtime agent/graph behavior, UI/setup/custom-source behavior, shared runner,
application-wide layer reporting, requirements, and the persistent `query_log`
schema are unchanged. Live evaluations append ordinary records to the existing
gitignored evaluation store. The pre-existing untracked history backup is untouched.

## Implemented contract and coverage

`result_match` measures full independent correctness. `terminal_verified` requires
explicit boolean success/valid flags and an explicit false out-of-range flag.
`passed` requires both plus the required physical-table layer. Correct data with
failed runtime verification remains independently diagnosable. Scores use Python
booleans. Out-of-range cases have a separate contract and use `None` for checks
that do not apply, rather than counting skipped checks as answer success.

Full comparison is order-independent, matches explicit keys and one-to-one value
aliases, requires mapped semantic columns and exact row identity, rejects duplicates/nulls/
nonfinite values, and compares counts exactly. Numeric strings and booleans are
rejected; integral numeric count representations are supported. Revenue/AOV use
`max(abs_tol, rel_tol * abs(expected))`, with explicit `0.005` absolute and zero
relative tolerances, accepting rounded two-decimal answers. Calendar digit strings, ISO dates, and timezone-free midnight
timestamps are supported without rounding values or weakening date filters.

Extra output columns are allowed by default; `result.exact_schema=True` opts into
exact schema. Explicit aliases match case-insensitively; ambiguous mappings still
fail. Date/year/month and single-geography keys have narrow declared aliases.
Each reference query records its canonical definition/formula/exclusions/grain/
intended layer, and contract changes invalidate the hashed `eval/test_suite.py`.

Golden row counts by answerable case: daily revenue 31; monthly revenue 12;
annual revenue 7; customer region 5; customer nation 25; supplier region 5;
default region 5; total revenue 1; monthly order volume 12; monthly AOV 12.
Both customer-region questions share a golden result. Every case was verified
against base facts; all eight distinct aggregate equivalents also matched.

SQLGlot resolves physical table references through scopes. Comments, string
literals, table aliases, shadowing CTEs, and unused CTEs cannot supply the expected
layer. Unknown sources, mixed aggregate/fact references, multiple statements, and
parse failures fail the layer check. Application logging is unchanged.

Controls cover the original false passes, missing/null/string statuses, failed
verification with correct data, out-of-range contradictions, malformed/nonnumeric
outputs, null keys/values, NaN/infinities, numeric strings, boolean/fractional counts,
missing/extra/duplicate rows or columns, ambiguous aliases, misplaced key/value
pairs, decoy numeric columns, zero/multiple scalar rows, malformed/missing oracles,
duplicate JSON/row keys, exact integer mismatch, zero/near-zero expectations, and
inclusive tolerance boundaries. Positive controls cover all ten fixtures,
reordered rows/columns, every declared value alias, equivalent date/month keys,
and integral count representations. A runner integration test scores a malformed
case as failed and then successfully scores the next case.

## Commands and outcomes

Python 3.13; pandas 3.0.3; NumPy 2.4.6; DuckDB/TPC-H 1.5.4;
SQLGlot 30.17.0; Streamlit 1.58.0; pytest 9.1.1.
All live runs use the exact requested model identifier **`gpt-4o`**, as hardcoded
in the unchanged runtime calls (temperature 0). No dated model snapshot was
configured; provider alias changes are outside this reproducibility claim.

The existing setup uses pip requirements. Runtime dependencies were already
available. `python -m pip --version` succeeded (26.1.2).
`python -m pip install pytest` first failed under restricted networking, then
succeeded with approved network access. No dependency file changed.

Verification invocations, including unsuccessful development/environment runs:

| Command | Outcome |
|---|---|
| `python -m pytest tests/test_eval_scoring.py -q --disable-warnings --tb=short` — first development run | 1 collection error from a test-file syntax typo; no tests executed. Fixed before subsequent checks. |
| Same command — sandbox retry | 151 passed, 4 temporary-directory setup errors, 0 skipped. |
| Same command with `--basetemp=.pytest_cache/scorer-tmp` | 151 passed, 4 temporary-directory setup errors, 0 skipped. |
| Same command with `--basetemp=__pycache__/batch1-scorer-temp` | Aborted during temporary-directory cleanup; no reliable final counts. |
| `python -m pytest tests -q --disable-warnings --tb=short --basetemp=__pycache__/batch1-full-temp` | Aborted with Windows sandbox permission errors; no reliable final counts. |
| `python -m pytest tests/test_eval_scoring.py -q --disable-warnings --tb=short` — outside sandbox | **155 passed, 0 failed, 0 skipped.** |
| `python -m pytest tests -q --disable-warnings --tb=short` — before adding freshness test module, outside sandbox | **256 passed, 2 subtests passed; 0 failed, 0 skipped.** |
| `$env:RUN_TPCH_REFERENCE_TESTS='1'; python -m pytest tests/test_eval_golden.py -q --disable-warnings --tb=short` | **21 passed, 0 failed, 0 skipped.** |
| `$env:RUN_TPCH_REFERENCE_TESTS='1'; python -m pytest tests -q --disable-warnings --tb=short` — outside sandbox | **277 passed, 2 subtests passed; 0 failed, 0 skipped.** Run three times: completed implementation, alias addition, and newline-portable provenance. |
| `python -m eval.golden --write` | Run twice, initially and after newline-portable hashing. Each passed **10 base-fact cases and 8 distinct aggregate cross-checks**. Explicit generation only. |
| `python -m eval.golden --check` | Run three times. Each passed **10 base-fact cases and 8 distinct aggregate cross-checks**, plus fixture/source hashes. |
| `python -m py_compile eval/scoring.py eval/golden.py eval/run_eval.py eval/test_suite.py tests/test_eval_scoring.py tests/test_eval_golden.py` | Run three times; all six files compiled successfully each time. |
| `git diff --check` | Passed; Git emitted only its Windows line-ending conversion notice. |
| `python -m eval.run_eval` — initial live run | **9 passed, 4 failed, 0 skipped**; 1.00 average attempts, reported cost $0.3111. |
| `$env:PYTHONUNBUFFERED='1'; python -m eval.run_eval` — final live run | **10 passed, 3 failed, 0 skipped**; 1.00 average attempts, reported cost $0.3111. |

Sandbox-created temporary test directories were inspected and removed using
workspace-bounded paths. No application safeguards were weakened. The historical
four Streamlit 1.64.0 disabled-save failures did not recur under installed Streamlit
1.58.0; compatibility with 1.64.0 remains unverified.

## Live evaluation

The initial live run rejected customer-region, customer-nation, and default-region
answers for using facts instead of required aggregates. Their data matched the
goldens and runtime verification succeeded. Monthly order volume used
`total_order_volume`, a reasonable explicit alias missing from the initial mapping.
That alias was added, with existing positive alias coverage exercising it. All
deterministic checks passed again before the final live run.

Pre-correction final live run: **10/13 (76.9%)**. All ten answerable result comparisons and their
runtime verification flags passed. Three required-layer checks failed. All three
out-of-range cases passed their separate contract. These are live results from
the final comparator/alias contract, not a replay or an inferred pass rate.

| # | Question | Initial run | Final run | Final explanation |
|---|---|---|---|---|
| 1 | What was daily revenue in January 1995? | PASS | PASS | Complete 31-row result and layer match. |
| 2 | What was revenue by month in 1995? | PASS | PASS | Complete 12-row result and layer match. |
| 3 | What was total revenue by year? | PASS | PASS | Complete 7-row result and layer match. |
| 4 | What is total revenue by customer region? | FAIL | FAIL | Correct result, verified by runtime; fact layer instead of aggregated. |
| 5 | What is total revenue by customer nation? | FAIL | FAIL | Correct result, verified by runtime; fact layer instead of aggregated. |
| 6 | What is total revenue by supplier region? | PASS | PASS | Complete 5-row result and required fact layer match. |
| 7 | What is total revenue by region? | FAIL | FAIL | Correct customer-region result, verified by runtime; fact layer instead of aggregated. |
| 8 | What is total revenue? | PASS | PASS | Scalar result and layer match. |
| 9 | What is order volume by month in 1995? | FAIL | PASS | All 12 exact counts and layer match; explicit total_order_volume alias accepted. |
| 10 | What is average order value by month in 1995? | PASS | PASS | All 12 AOV values and layer match. |
| 11 | What was revenue last year? | PASS | PASS | Valid out-of-range termination. |
| 12 | What is revenue this month? | PASS | PASS | Valid out-of-range termination. |
| 13 | What was revenue in 2024? | PASS | PASS | Valid out-of-range termination. |

The live process completed with exit code 0; its score report still explicitly
records the three behavioral failures. The two runs together reported $0.6222
in estimated cost, using the unchanged runtime cost accounting.

## Remaining limits and owner review

- Owner review of reference SQL, frozen answers, and tolerance choices is pending.
- TPC-H sf=0.1 has no cancelled C orders; these results cannot verify cancellation
  exclusion. The reference SQL preserves the global exclusion rule despite its
  omission in the bundled supplier SQL example.
- Layer checking identifies physical table use; it is not a proof of every
  source's contribution, join correctness, or query execution history.
- Declared semantic columns/aliases are required; arbitrary aliases remain
  unsupported. Extra output columns are allowed unless exact_schema is enabled.
- Deferred runtime validation parsing, empty-result behavior, reflection ownership,
  retry/cost behavior, and custom-source reporting remain unchanged.
- No jaffle_shop data/context was inspected or reconstructed. No manual Streamlit
  verification is required for these evaluator-only changes; the existing AppTest
  suite was run, but no manual browser verification was performed.
