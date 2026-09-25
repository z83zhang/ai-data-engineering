"""Explicit TPC-H fixture generation/checking; never called by live evaluation.

python -m eval.golden --write   # creates a diff requiring review
python -m eval.golden --check   # checks committed rows and provenance
"""

import argparse
import hashlib
import json
import subprocess
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from agent import setup_database
from eval.scoring import GOLDEN_PATH, compare_result, load_golden, validate_contract
from eval.test_suite import TEST_CASES


ROOT = Path(__file__).resolve().parent.parent
REFERENCE_PATH = GOLDEN_PATH.with_name("reference_queries.json")
PROVENANCE_PATH = GOLDEN_PATH.with_name("provenance.json")


def file_sha256(path):
    """Hash text with normalized newlines so Git CRLF conversion is harmless."""
    return hashlib.sha256(Path(path).read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def source_hashes():
    paths = [REFERENCE_PATH, ROOT / "eval/test_suite.py", ROOT / "agent.py",
             *sorted((ROOT / "context").glob("*"))]
    return {str(path.relative_to(ROOT)).replace("\\", "/"): file_sha256(path)
            for path in paths if path.is_file()}


def json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Unsupported fixture value: {type(value)}")


def reference_results(conn):
    queries = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    result = {}
    for name, query in queries.items():
        cursor = conn.execute(query["reference_sql"])
        columns = [column[0] for column in cursor.description]
        result[name] = [dict(zip(columns, row)) for row in cursor.fetchall()]
    # Compare exactly the JSON representation that will be frozen.
    return json.loads(json.dumps(result, default=json_value))


def check_results(conn, golden):
    """Check all ten cases independently; also check eight aggregate equivalents."""
    queries = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    references, aggregates = 0, set()
    for case in TEST_CASES:
        if case["failure_mode"] == "out_of_range":
            continue
        rows = validate_contract(case, golden)
        name = case["result"]["fixture"]
        for label in ("reference_sql", "aggregate_sql"):
            sql = queries[name][label]
            if sql is None:
                continue
            matched, reasons = compare_result(conn.execute(sql).fetchdf(), case["result"], rows)
            if not matched:
                raise ValueError(f"{case['id']} {label}: {reasons}")
            if label == "reference_sql":
                references += 1
            else:
                aggregates.add(name)
    return references, len(aggregates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    conn = setup_database()
    try:
        golden = reference_results(conn) if args.write else load_golden()
        references, aggregates = check_results(conn, golden)
        if args.write:
            provenance = {
                "repository_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "duckdb_version": duckdb.__version__,
                "tpch_extension": conn.execute("SELECT extension_version FROM duckdb_extensions() WHERE extension_name='tpch'").fetchone()[0],
                "dbgen": {"sf": 0.1, "other_parameters": "DuckDB defaults"},
                "source_sha256": source_hashes(),
                "order_status_counts": dict(conn.execute("SELECT o_orderstatus, COUNT(*) FROM orders GROUP BY 1 ORDER BY 1").fetchall()),
                "reference_cases_checked": references,
                "distinct_aggregate_results_checked": aggregates,
                "review": "Implementation checked against bundled metric definitions; owner review pending. Reference SQL authored independently of runtime SQL generation.",
                "tolerances": "Counts exact. Revenue/AOV: absolute tolerance 0.005, relative tolerance 0; accepts rounding to two decimals without a magnitude-dependent allowance.",
                "limitations": "No C orders: results cannot test cancellation-exclusion enforcement. Supplier SQL includes the global exclusion despite its omission in the bundled supplier example. No supplier aggregate exists.",
            }
            GOLDEN_PATH.write_text(json.dumps(golden, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            provenance["fixture_sha256"] = file_sha256(GOLDEN_PATH)
            PROVENANCE_PATH.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        else:
            provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
            if provenance["source_sha256"] != source_hashes():
                raise ValueError("Reference/context/demo-setup source hashes changed; review provenance")
            if provenance["fixture_sha256"] != file_sha256(GOLDEN_PATH):
                raise ValueError("Fixture checksum changed; review provenance")
        print(f"PASS: {references} base-fact case checks; {aggregates} distinct aggregate cross-checks")
        print(f"DuckDB {duckdb.__version__}; frozen generator version: "
              f"{provenance['duckdb_version']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
