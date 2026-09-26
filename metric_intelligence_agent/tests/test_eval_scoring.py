"""Offline adversarial tests: no model, database generation, or network."""

from copy import deepcopy
from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from eval.scoring import (
    ContractError, compare_result, evaluation_layer, load_golden, score_result, validate_contract,
)
from eval.test_suite import TEST_CASES


GOLDEN = load_golden()
ANSWERABLE = TEST_CASES[:10]


@pytest.fixture
def scalar_contract():
    return {"keys": {}, "values": {
        "total": {"type": "integer", "aliases": ["total_count"], "abs_tol": 0, "rel_tol": 0}
    }}


@pytest.mark.parametrize("column", ["count_star()", "COUNT(*)", 0])
@pytest.mark.parametrize("exact_schema", [False, True])
def test_unnamed_single_scalar_matches_by_structure(scalar_contract, column, exact_schema):
    scalar_contract["exact_schema"] = exact_schema
    assert compare_result(pd.DataFrame({column: [7]}), scalar_contract, [{"total": 7}]) == (True, [])


@pytest.mark.parametrize("value", [8, 7.1, "7", None, float("inf")])
def test_scalar_fallback_preserves_value_and_type_checks(scalar_contract, value):
    assert not compare_result(pd.DataFrame({"count_star()": [value]}), scalar_contract, [{"total": 7}])[0]


@pytest.mark.parametrize("data", [
    pd.DataFrame({"x": [7], "y": [7]}),
    pd.DataFrame({"x": [7, 7]}),
    pd.DataFrame({"x": []}),
])
def test_scalar_fallback_rejects_other_shapes(scalar_contract, data):
    matched, reasons = compare_result(data, scalar_contract, [{"total": 7}])
    assert not matched and "Missing or ambiguous column" in reasons[0]


def test_scalar_fallback_never_supplies_multiple_value_roles(scalar_contract):
    scalar_contract["values"]["second"] = {"type": "integer", "abs_tol": 0, "rel_tol": 0}
    assert not compare_result(pd.DataFrame({"x": [7]}), scalar_contract, [{"total": 7, "second": 7}])[0]


def test_scalar_fallback_never_supplies_grouped_roles(scalar_contract):
    scalar_contract["keys"] = {"status": {"type": "text"}}
    rows = [{"status": "completed", "total": 7}]
    assert not compare_result(pd.DataFrame({"x": [7]}), scalar_contract, rows)[0]
    assert not compare_result(pd.DataFrame({"status": ["completed"], "x": [7]}), scalar_contract, rows)[0]
    assert compare_result(pd.DataFrame({"status": ["completed"], "TOTAL_COUNT": [7]}), scalar_contract, rows) == (True, [])


def test_explicit_aliases_keep_precedence_and_ambiguity_checks(scalar_contract):
    rows = [{"total": 7}]
    assert compare_result(pd.DataFrame({"TOTAL_COUNT": [7], "extra": [99]}), scalar_contract, rows) == (True, [])
    assert not compare_result(pd.DataFrame({"TOTAL_COUNT": [99], "extra": [7]}), scalar_contract, rows)[0]
    assert not compare_result(pd.DataFrame({"total": [7], "total_count": [7]}), scalar_contract, rows)[0]


def frame(case):
    return pd.DataFrame(deepcopy(GOLDEN[case["result"]["fixture"]]))


def state(case, data=None):
    return dict(data=frame(case) if data is None else data,
                sql="SELECT * FROM lineitem" if case["expected_layer"] == "fact" else "SELECT * FROM agg_monthly_sales",
                success=True, valid=True, out_of_range=False, attempt=1, cost_usd=0)


def score(case, data):
    return score_result(state(case, data), case, GOLDEN)


@pytest.mark.parametrize("case", ANSWERABLE, ids=lambda c: c["id"])
def test_all_golden_cases_and_reordered_results_pass(case):
    data = frame(case)
    assert score(case, data)["passed"] is True
    assert score(case, data.iloc[::-1, ::-1])["passed"] is True


@pytest.mark.parametrize("case", ANSWERABLE, ids=lambda c: c["id"])
def test_every_case_rejects_changed_value(case):
    data = frame(case)
    column = next(iter(case["result"]["values"]))
    data.loc[0, column] += 100
    result = score(case, data)
    assert result["result_match"] is False
    assert result["passed"] is False
    assert result["reasons"]


@pytest.mark.parametrize("field", ["success", "valid", "out_of_range"])
@pytest.mark.parametrize("value", [None, "False", "True", 1, 0, "missing"])
def test_status_requires_explicit_booleans(field, value):
    case = ANSWERABLE[7]
    terminal = state(case)
    if value == "missing":
        terminal.pop(field)
    else:
        terminal[field] = value
    result = score_result(terminal, case, GOLDEN)
    assert result["result_match"] is True
    assert result["terminal_verified"] is False
    assert result["passed"] is False


@pytest.mark.parametrize("field,value", [("success", False), ("valid", False), ("out_of_range", True)])
def test_correct_result_does_not_override_failed_terminal_status(field, value):
    case = ANSWERABLE[7]
    terminal = state(case)
    terminal[field] = value
    result = score_result(terminal, case, GOLDEN)
    assert result["result_match"] is True
    assert result["passed"] is False


def test_numpy_booleans_normalized_and_wrong_numpy_value_fails():
    case = ANSWERABLE[7]
    terminal = state(case, pd.DataFrame({"revenue": [np.float64(1.0)]}))
    terminal.update(success=np.bool_(True), valid=np.bool_(True), out_of_range=np.bool_(False))
    result = score_result(terminal, case, GOLDEN)
    assert result["result_match"] is False
    assert result["passed"] is False
    assert all(type(result[k]) is bool for k in ("result_match", "terminal_verified", "layer_match", "passed"))


@pytest.mark.parametrize("bad", [None, {}, [], "answer", pd.DataFrame(), pd.DataFrame({"revenue": ["bad"]})])
def test_missing_or_malformed_data_is_a_normal_failure(bad):
    case = ANSWERABLE[7]
    terminal = state(case)
    terminal["data"] = bad
    assert score_result(terminal, case, GOLDEN)["passed"] is False


@pytest.mark.parametrize("value", [None, pd.NA, np.nan, np.inf, -np.inf, "1", True, np.bool_(False), complex(1, 2)])
def test_bad_numeric_values_fail(value):
    case = ANSWERABLE[0]
    data = frame(case).astype({"revenue": object})
    data.loc[1, "revenue"] = value
    assert score(case, data)["passed"] is False


@pytest.mark.parametrize("change", ["missing_row", "extra_row", "duplicate_key", "null_key", "wrong_key", "missing_column", "duplicate_column", "ambiguous_alias"])
def test_shape_keys_and_columns_fail_closed(change):
    case = ANSWERABLE[0]
    data = frame(case)
    if change == "missing_row": data = data.iloc[:-1]
    elif change == "extra_row": data = pd.concat([data, data.iloc[:1]], ignore_index=True)
    elif change == "duplicate_key": data.loc[1, "order_date"] = data.loc[0, "order_date"]
    elif change == "null_key": data.loc[1, "order_date"] = None
    elif change == "wrong_key": data.loc[1, "order_date"] = "1996-01-02"
    elif change == "missing_column": data = data.drop(columns="revenue")
    elif change == "duplicate_column": data = pd.concat([data, data[["revenue"]]], axis=1)
    elif change == "ambiguous_alias": data["total_revenue"] = data["revenue"]
    assert score(case, data)["passed"] is False


def test_values_swapped_between_valid_keys_fail():
    case = ANSWERABLE[3]
    data = frame(case)
    data["revenue"] = data["revenue"].iloc[::-1].to_numpy()
    assert score(case, data)["result_match"] is False


def test_decoy_correct_column_cannot_hide_wrong_metric():
    case = ANSWERABLE[7]
    data = frame(case)
    data["decoy"] = data["revenue"]
    data["revenue"] = 1.0
    assert score(case, data)["passed"] is False


@pytest.mark.parametrize("exact_schema", [False, True])
def test_extra_columns_follow_explicit_schema_policy(exact_schema):
    case = deepcopy(ANSWERABLE[0])
    if exact_schema:
        case["result"]["exact_schema"] = True
    data = frame(case)
    data["unrelated"] = None
    assert score(case, data)["passed"] is (not exact_schema)
    assert score(case, frame(case))["passed"] is True


@pytest.mark.parametrize("case", ANSWERABLE, ids=lambda c: c["id"])
def test_case_insensitive_explicit_key_and_value_aliases(case):
    specs = {**case["result"]["keys"], **case["result"]["values"]}
    renamed = {name: spec.get("aliases", [name])[0].upper() for name, spec in specs.items()}
    assert score(case, frame(case).rename(columns=renamed))["passed"] is True


def test_case_insensitive_ambiguity_and_fuzzy_names_fail():
    case = ANSWERABLE[0]
    data = frame(case)
    data["REVENUE"] = data["revenue"]
    assert score(case, data)["passed"] is False
    assert score(case, frame(case).rename(columns={"revenue": "revenu"}))["passed"] is False
    contract = deepcopy(case)
    contract["result"]["values"]["revenue"]["aliases"].append("DATE")
    with pytest.raises(ContractError):
        validate_contract(contract, GOLDEN)


@pytest.mark.parametrize("case", [c for c in ANSWERABLE if c["id"] != "monthly_orders"], ids=lambda c: c["id"])
def test_two_decimal_rounded_complete_results_pass(case):
    data = frame(case)
    for name in case["result"]["values"]:
        data[name] = data[name].map(lambda value: Decimal(str(value)).quantize(Decimal("0.01")))
    assert score(case, data)["passed"] is True


@pytest.mark.parametrize("case", [ANSWERABLE[7], ANSWERABLE[9]], ids=lambda c: c["id"])
@pytest.mark.parametrize("offset,passed", [("0.004999", True), ("0.005", True), ("0.005001", False), ("-0.005001", False)])
def test_currency_precision_boundary_uses_final_contract(case, offset, passed):
    data = frame(case)
    name = next(iter(case["result"]["values"]))
    data[name] = data[name].map(lambda value: Decimal(str(value)))
    data.loc[0, name] += Decimal(offset)
    assert score(case, data)["passed"] is passed


def test_original_daily_monthly_and_aov_false_passes():
    daily, orders, aov = ANSWERABLE[0], ANSWERABLE[8], ANSWERABLE[9]
    data = frame(daily); data["revenue"] = 1.0
    assert score(daily, data)["passed"] is False
    data = frame(orders)
    total = data["order_volume"].sum()
    data["order_volume"] = 0; data.loc[0, "order_volume"] = total
    assert score(orders, data)["passed"] is False
    data = frame(aov); data.loc[1:, "average_order_value"] = 0.0
    assert score(aov, data)["passed"] is False


@pytest.mark.parametrize("rows", [0, 2])
def test_scalar_must_have_exactly_one_row(rows):
    case = ANSWERABLE[7]
    data = pd.concat([frame(case)] * rows, ignore_index=True) if rows else frame(case).iloc[:0]
    assert score(case, data)["passed"] is False


@pytest.mark.parametrize("delta", [1, 0.1])
def test_counts_are_exact_and_integral(delta):
    case = ANSWERABLE[8]
    data = frame(case).astype({"order_volume": float})
    data.loc[0, "order_volume"] += delta
    assert score(case, data)["passed"] is False


def test_integral_numeric_counts_are_accepted():
    case = ANSWERABLE[8]
    assert score(case, frame(case).astype({"order_volume": float}))["passed"] is True


@pytest.mark.parametrize("convert", [lambda v: v, date.fromisoformat, pd.Timestamp, lambda v: v + "T00:00:00"])
def test_equivalent_dates_pass(convert):
    case = ANSWERABLE[0]
    data = frame(case); data["order_date"] = data["order_date"].map(convert)
    assert score(case, data)["passed"] is True


@pytest.mark.parametrize("convert", [float, lambda v: f"{v:02d}"])
def test_equivalent_month_keys_pass(convert):
    case = ANSWERABLE[1]
    data = frame(case); data["order_month"] = data["order_month"].map(convert)
    assert score(case, data)["passed"] is True


@pytest.mark.parametrize("value", ["1995-01-01T12:00:00", "1995-01-01T00:00:00Z", "01/01/1995", pd.NaT])
def test_lossy_or_ambiguous_date_conversion_fails(value):
    case = ANSWERABLE[0]
    data = frame(case); data.loc[0, "order_date"] = value
    assert score(case, data)["passed"] is False


@pytest.mark.parametrize("case", ANSWERABLE, ids=lambda c: c["id"])
def test_explicit_metric_aliases_pass(case):
    column, spec = next(iter(case["result"]["values"].items()))
    for alias in spec["aliases"]:
        assert score(case, frame(case).rename(columns={column: alias}))["passed"] is True


@pytest.mark.parametrize("target,actual,abs_tol,rel_tol,passed", [
    (0, "0", 0, 0, True), (0, "0.01", 0.01, 0, True),
    (0, "0.010001", 0.01, 0, False), (0, "0.0001", 0, 0.01, False),
    (1e-9, "0.000000001001", 1e-12, 0, True),
    (1e-9, "0.000000001002", 1e-12, 0, False),
    (100, "101", 0, 0.01, True), (100, "101.0001", 0, 0.01, False),
    (-100, "-101", 0, 0.01, True), (100, "99", 0, 0.01, True),
    (100, "101.5", 1, 0.01, False),
])
def test_tolerance_boundaries(target, actual, abs_tol, rel_tol, passed):
    case = deepcopy(ANSWERABLE[7]); spec = case["result"]["values"]["revenue"]
    spec.update(abs_tol=abs_tol, rel_tol=rel_tol)
    golden = {case["result"]["fixture"]: [{"revenue": target}]}
    terminal = state(case, pd.DataFrame({"revenue": [Decimal(actual)]}))
    assert score_result(terminal, case, golden)["passed"] is passed


@pytest.mark.parametrize("change", ["missing_fixture", "empty_rows", "duplicate_key", "missing_value", "null_value", "bad_rows", "overlap_alias", "negative_tolerance", "missing_tolerance", "nan_expected"])
def test_malformed_oracles_are_explicit_configuration_errors(change):
    case = deepcopy(ANSWERABLE[0]); golden = deepcopy(GOLDEN)
    rows = golden[case["result"]["fixture"]]
    spec = case["result"]["values"]["revenue"]
    if change == "missing_fixture": golden = {}
    elif change == "empty_rows": rows.clear()
    elif change == "duplicate_key": rows.append(deepcopy(rows[0]))
    elif change == "missing_value": rows[0].pop("revenue")
    elif change == "null_value": rows[0]["revenue"] = None
    elif change == "bad_rows": golden[case["result"]["fixture"]] = "bad"
    elif change == "overlap_alias": spec["aliases"].append("order_date")
    elif change == "negative_tolerance": spec["abs_tol"] = -1
    elif change == "missing_tolerance": spec.pop("abs_tol")
    elif change == "nan_expected": rows[0]["revenue"] = float("nan")
    with pytest.raises(ContractError): validate_contract(case, golden)
    result = score_result(state(case), case, golden)
    assert result["passed"] is False
    assert "Invalid result contract" in result["reasons"][0]


@pytest.mark.parametrize("contents", [None, '{"x": [], "x": []}', '{', '[]'])
def test_missing_or_malformed_json_fails(tmp_path, contents):
    path = tmp_path / "golden.json"
    if contents is not None: path.write_text(contents, encoding="utf-8")
    with pytest.raises(ContractError): load_golden(path)


@pytest.mark.parametrize("sql,expected", [
    ("SELECT SUM(revenue) FROM agg_monthly_sales", "aggregated"),
    ('SELECT * FROM main."AGG_DAILY_SALES"', "aggregated"),
    ("WITH x AS (SELECT * FROM agg_monthly_sales) SELECT * FROM x", "aggregated"),
    ("WITH agg_monthly_sales AS (SELECT * FROM orders) SELECT * FROM agg_monthly_sales", "fact"),
    ("SELECT * FROM orders -- agg_monthly_sales", "fact"),
    ("SELECT 'agg_monthly_sales' FROM orders", "fact"),
    ("SELECT * FROM orders AS agg_monthly_sales", "fact"),
    ("WITH unused AS (SELECT * FROM agg_monthly_sales) SELECT * FROM orders", "fact"),
    ("SELECT * FROM orders WHERE EXISTS (SELECT 1 FROM agg_monthly_sales)", None),
    ("SELECT * FROM agg_monthly_sales UNION ALL SELECT * FROM orders", None),
    ("SELECT * FROM unknown", None), ("SELECT 'agg_monthly_sales'", None),
    ("SELECT * FROM other.agg_monthly_sales", None),
    ("SELECT * FROM other.main.agg_monthly_sales", None),
    ("SELECT * FROM agg_monthly_sales; SELECT * FROM orders", None),
    ("SELECT * FROM", None), (None, None),
])
def test_physical_table_layers(sql, expected):
    assert evaluation_layer(sql) == expected
    if expected != "aggregated":
        case = ANSWERABLE[7]; terminal = state(case); terminal["sql"] = sql
        assert score_result(terminal, case, GOLDEN)["passed"] is False


def out_of_range_state():
    return dict(out_of_range=True, data=None, success=False, valid=False,
                sql="OUT_OF_RANGE: demo data ends in 1998")


@pytest.mark.parametrize("case", TEST_CASES[10:], ids=lambda c: c["id"])
def test_out_of_range_positive(case):
    assert score_result(out_of_range_state(), case, GOLDEN)["passed"] is True


@pytest.mark.parametrize("field,value", [
    ("data", pd.DataFrame()), ("data", pd.DataFrame({"x": [1]})),
    ("out_of_range", False), ("out_of_range", "True"), ("success", True),
    ("valid", True), ("sql", "SELECT 1"), ("sql", "OUT_OF_RANGE:"),
    ("success", None), ("valid", "False"),
])
def test_out_of_range_contradictions_fail(field, value):
    terminal = out_of_range_state(); terminal[field] = value
    assert score_result(terminal, TEST_CASES[10], GOLDEN)["passed"] is False


@pytest.mark.parametrize("field", ["data", "success", "valid", "out_of_range", "sql"])
def test_out_of_range_missing_fields_fail(field):
    terminal = out_of_range_state(); terminal.pop(field)
    assert score_result(terminal, TEST_CASES[10], GOLDEN)["passed"] is False


def test_live_runner_continues_after_malformed_result(monkeypatch, capsys):
    import eval.run_eval as runner
    case = ANSWERABLE[7]
    bad = state(case, pd.DataFrame({"revenue": ["not numeric"]}))
    states = iter([{**bad, "model": "gpt-4o"}, {**state(case), "model": "gpt-4o"}])
    class Connection:
        def close(self): pass
    monkeypatch.setattr(runner, "TEST_CASES", [case, case])
    monkeypatch.setattr(runner, "setup_database", Connection)
    monkeypatch.setattr(runner, "setup_eval_db", Connection)
    monkeypatch.setattr(runner, "get_date_range", lambda conn: (None, None))
    monkeypatch.setattr(runner, "load_context", lambda *args: "")
    monkeypatch.setattr(runner, "build_graph", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "run_question", lambda *args, **kwargs: (next(states), "id"))
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)
    runner.run_eval()
    output = capsys.readouterr().out
    assert "[FAIL] 01/2" in output and "[PASS] 02/2" in output
    assert "Total pass rate: 1/2" in output
    assert "Expected a number" in output
