"""Provenance checks and reference checks when the local TPC-H extension exists."""

import json
import duckdb

import pytest

from eval.golden import PROVENANCE_PATH, REFERENCE_PATH, file_sha256, source_hashes
from eval.scoring import GOLDEN_PATH, compare_result, load_golden, validate_contract
from eval.test_suite import TEST_CASES


CASES = TEST_CASES[:10]
QUERIES = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))


def test_frozen_fixture_and_source_checksums():
    provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    assert provenance["source_sha256"] == source_hashes()
    assert provenance["fixture_sha256"] == file_sha256(GOLDEN_PATH)
    assert provenance["dbgen"]["sf"] == 0.1
    assert "eval/test_suite.py" in provenance["source_sha256"]


def test_complete_coverage_and_no_unreferenced_goldens():
    golden = load_golden()
    assert len(CASES) == 10
    assert {case["result"]["fixture"] for case in CASES} == set(golden) == set(QUERIES)
    assert [len(validate_contract(case, golden)) for case in CASES] == [31, 12, 7, 5, 25, 5, 5, 1, 12, 12]


def test_each_reference_has_canonical_semantic_provenance():
    for case in CASES:
        contract = QUERIES[case["result"]["fixture"]]["canonical_contract"]
        assert contract["definition_file"] == "context/metric_definitions.md"
        assert contract["sections"] and contract["formula"]
        assert contract["exclusions"] == ["o_orderstatus <> 'C'"]
        assert contract["grain"] == list(case["result"]["keys"])
        assert contract["intended_layer"] == case["expected_layer"]


def test_frozen_anchor_values_and_geography_totals():
    golden = load_golden()
    total = golden["total_revenue"][0]["revenue"]
    assert total == pytest.approx(20535072231.415, rel=0, abs=1e-5)
    assert sum(row["order_volume"] for row in golden["monthly_orders"]) == 22909
    for name in ("customer_region", "customer_nation", "supplier_region", "annual_revenue"):
        assert sum(row["revenue"] for row in golden[name]) == pytest.approx(total, rel=1e-12)


@pytest.fixture(scope="module")
def reference_connection():
    # Probe only extension availability; errors in setup/reference SQL must fail.
    probe = duckdb.connect(":memory:")
    try:
        probe.execute("LOAD tpch")
    except duckdb.Error as exc:
        pytest.skip(f"Local TPC-H extension unavailable: {exc}")
    finally:
        probe.close()
    from agent import setup_database
    conn = setup_database()
    yield conn
    conn.close()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_base_fact_reference_matches_frozen_rows(reference_connection, case):
    rows = validate_contract(case, load_golden())
    sql = QUERIES[case["result"]["fixture"]]["reference_sql"]
    matched, reasons = compare_result(reference_connection.execute(sql).fetchdf(), case["result"], rows)
    assert matched, reasons


@pytest.mark.parametrize("name", [name for name, query in QUERIES.items() if query["aggregate_sql"]])
def test_aggregate_cross_check(reference_connection, name):
    case = next(case for case in CASES if case["result"]["fixture"] == name)
    rows = validate_contract(case, load_golden())
    data = reference_connection.execute(QUERIES[name]["aggregate_sql"]).fetchdf()
    matched, reasons = compare_result(data, case["result"], rows)
    assert matched, reasons
