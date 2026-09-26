"""Explicit candidate generation and freshness checking; never calls a model."""
import copy
import json

from connectors.duckdb import DuckDBConnector
from eval.custom_suite import digest, inputs, parser, validate_case, write_new
from eval.golden import json_value
from eval.scoring import compare_result


def candidate(db, context_dir, suite_path, output):
    suite, _, _, provenance = inputs(db, context_dir, suite_path, candidate=True)
    suite = copy.deepcopy(suite)
    conn = DuckDBConnector().connect({"path": db})
    try:
        for case in suite["cases"]:
            if case["reference"]["kind"] == "sql":
                cursor = conn.execute(case["reference"]["sql"])
                names = [column[0] for column in cursor.description]
                if len(names) != len(set(names)):
                    raise ValueError(f"{case['id']}: duplicate reference output columns")
                rows = [dict(zip(names, row)) for row in cursor.fetchall()]
                case["expected_rows"] = json.loads(json.dumps(rows, default=json_value, allow_nan=False))
            validate_case(case)  # Manual references must already contain explicit rows.
    finally:
        conn.close()
    provenance["expected_sha256"] = digest({c["id"]: c["expected_rows"] for c in suite["cases"]})
    suite["provenance"] = provenance
    suite["approval"] = {"status": "requires_owner_review", "reviewer": "", "note": "Review reference semantics and expected rows independently before approval."}
    write_new(output, suite)
    return suite


def check(db, context_dir, suite_path):
    suite, _, _, provenance = inputs(db, context_dir, suite_path, check_drift=True)
    conn = DuckDBConnector().connect({"path": db})
    outcomes = []
    if suite.get("provenance") != provenance:
        outcomes.append({"id": "__provenance__", "passed": False,
                         "reasons": ["DB/context/suite/expected rows/DuckDB provenance changed"]})
    try:
        for case in suite["cases"]:
            if case["reference"]["kind"] != "sql":
                outcomes.append({"id": case["id"], "passed": False, "reasons": ["Manual golden needs owner re-review; no executable reference"]})
                continue
            try:
                passed, reasons = compare_result(conn.execute(case["reference"]["sql"]).df(), case["result"], case["expected_rows"])
            except Exception as exc:
                passed, reasons = False, [str(exc)]
            outcomes.append({"id": case["id"], "passed": passed, "reasons": reasons})
    finally:
        conn.close()
    return outcomes


def main(argv=None):
    cli = parser(__doc__)
    modes = cli.add_mutually_exclusive_group(required=True)
    modes.add_argument("--candidate", action="store_true")
    modes.add_argument("--check", action="store_true")
    cli.add_argument("--output", type=str)
    args = cli.parse_args(argv)
    if args.candidate and not args.output:
        cli.error("--candidate requires a new --output path")
    if args.check and args.output:
        cli.error("--check does not write output")
    try:
        if args.candidate:
            candidate(args.db, args.context_dir, args.suite, args.output)
            print("Candidate written; owner review required. No approval was granted.")
            return 0
        outcomes = check(args.db, args.context_dir, args.suite)
        print(json.dumps(outcomes, indent=2))
        return 0 if all(item["passed"] for item in outcomes) else 1
    except (ValueError, OSError, ConnectionError) as exc:
        cli.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
