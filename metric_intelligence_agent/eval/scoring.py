"""Deterministic TPC-H evaluation only; no runtime or model dependencies."""

import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from numbers import Number
from pathlib import Path

import numpy as np
import pandas as pd
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, build_scope


GOLDEN_PATH = Path(__file__).parent / "fixtures" / "tpch_sf01.json"


class ContractError(ValueError):
    """A missing or invalid evaluation oracle, never a skipped check."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_golden(path=GOLDEN_PATH):
    try:
        with Path(path).open(encoding="utf-8") as stream:
            result = json.load(stream, object_pairs_hook=_unique_object)
        if not isinstance(result, dict) or not result:
            raise ContractError("Golden fixture must be a nonempty object")
        return result
    except (OSError, ValueError) as exc:
        raise ContractError(f"Cannot load golden fixture: {exc}") from exc


def _number(value):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (Number, Decimal)):
        raise ValueError("Expected a number, not a string or boolean")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid numeric value") from exc
    if not number.is_finite():
        raise ValueError("Null or nonfinite numeric value")
    return number


def _normalize(value, kind):
    if kind in ("number", "integer", "year", "month"):
        # Calendar keys alone accept digit strings, e.g. month '01'.
        if kind in ("year", "month") and isinstance(value, str) and re.fullmatch(r"\d{1,4}", value):
            value = int(value)
        number = _number(value)
        if kind != "number" and number != number.to_integral_value():
            raise ValueError("Nonintegral count or calendar key")
        if kind == "month" and not 1 <= number <= 12:
            raise ValueError("Month outside 1..12")
        if kind == "year" and not 1 <= number <= 9999:
            raise ValueError("Year outside 1..9999")
        return number
    if kind == "date":
        if isinstance(value, str):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ]00:00:00(?:\.0+)?)?", value):
                raise ValueError("Expected ISO date or midnight timestamp")
            value = datetime.fromisoformat(value)
        if isinstance(value, datetime):
            if value.tzinfo is not None or value.time() != datetime.min.time():
                raise ValueError("Date key must be timezone-free midnight")
            value = value.date()
        if not isinstance(value, date) or pd.isna(value):
            raise ValueError("Invalid date key")
        return value.isoformat()
    if kind == "text" and isinstance(value, str) and value:
        return value
    raise ValueError(f"Invalid {kind} value")


def validate_contract(case, golden):
    """Validate trusted configuration separately from untrusted result data."""
    try:
        contract = case["result"]
        if type(contract.get("exact_schema", False)) is not bool:
            raise ValueError("exact_schema must be a boolean")
        keys, values = contract["keys"], contract["values"]
        if not isinstance(keys, dict) or not isinstance(values, dict) or not values:
            raise ValueError("Keys/values must be mappings with at least one value")
        if set(keys) & set(values):
            raise ValueError("Keys and values overlap")
        aliases = []
        for name, spec in {**keys, **values}.items():
            if not isinstance(name, str) or not name or spec["type"] not in {"text", "date", "year", "month", "integer", "number"}:
                raise ValueError("Invalid column specification")
            accepted = spec.get("aliases", [])
            if not isinstance(accepted, list) or any(not isinstance(a, str) or not a for a in accepted):
                raise ValueError("Aliases must be nonempty strings")
            aliases.extend(alias.casefold() for alias in [name, *accepted])
            if name in values:
                if spec["type"] not in {"integer", "number"}:
                    raise ValueError("Metric values must be numeric")
                for field in ("abs_tol", "rel_tol"):
                    tolerance = _number(spec[field])
                    if tolerance < 0 or (spec["type"] == "integer" and tolerance != 0):
                        raise ValueError("Invalid tolerance")
        if len(aliases) != len(set(aliases)):
            raise ValueError("Ambiguous accepted aliases")
        rows = golden[contract["fixture"]]
        if not isinstance(rows, list) or not rows or (not keys and len(rows) != 1):
            raise ValueError("Expected nonempty rows, or exactly one scalar row")
        columns = {**keys, **values}
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(columns):
                raise ValueError("Fixture columns do not match contract")
            normalized = {name: _normalize(row[name], spec["type"]) for name, spec in columns.items()}
            key = tuple(normalized[name] for name in keys)
            if key in seen:
                raise ValueError("Duplicate fixture key")
            seen.add(key)
        return rows
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ContractError(f"Invalid result contract: {exc}") from exc


def compare_result(data, contract, rows):
    """Compare a DataFrame with a validated contract; return bool and diagnostics."""
    try:
        if not isinstance(data, pd.DataFrame):
            raise ValueError("Missing or non-DataFrame result")
        if not data.columns.is_unique:
            raise ValueError("Duplicate output column labels")
        specs = {**contract["keys"], **contract["values"]}
        mapping = {}
        for name, spec in specs.items():
            accepted = {alias.casefold() for alias in [name, *spec.get("aliases", [])]}
            matches = [column for column in data.columns
                       if isinstance(column, str) and column.casefold() in accepted]
            if len(matches) != 1:
                raise ValueError(f"Missing or ambiguous column: {name}")
            mapping[name] = matches[0]
        if contract.get("exact_schema", False) and set(data.columns) != set(mapping.values()):
            raise ValueError("Unexpected output columns")
        if len(data) != len(rows):
            raise ValueError(f"Row count: expected {len(rows)}, got {len(data)}")
        expected = {tuple(_normalize(row[k], contract["keys"][k]["type"]) for k in contract["keys"]): row for row in rows}
        seen = set()
        for raw in data.to_dict("records"):
            row = {name: _normalize(raw[column], specs[name]["type"]) for name, column in mapping.items()}
            key = tuple(row[k] for k in contract["keys"])
            if key in seen:
                raise ValueError(f"Duplicate result key: {key}")
            seen.add(key)
            if key not in expected:
                raise ValueError(f"Unexpected result key: {key}")
            for name, spec in contract["values"].items():
                target = _normalize(expected[key][name], spec["type"])
                tolerance = max(_number(spec["abs_tol"]), _number(spec["rel_tol"]) * abs(target))
                if abs(row[name] - target) > tolerance:
                    raise ValueError(f"Value mismatch at {key}, {name}: expected {target}, got {row[name]}")
        if seen != set(expected):
            raise ValueError("Missing result keys")
        return True, []
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        return False, [str(exc)]


def evaluation_layer(sql):
    """Classify referenced demo tables, resolving CTE scope and ignoring unused CTEs.

    Aggregate + fact mixtures are not accepted as an aggregate-only answer.
    Unknown/catalog-qualified sources and parse failures fail closed.
    """
    try:
        statements = sqlglot.parse(sql, read="duckdb")
        if len(statements) != 1 or not isinstance(statements[0], exp.Query):
            return None
        root = build_scope(statements[0])
        if root is None:
            return None
        tables, visited = set(), set()

        def visit(scope):
            if id(scope) in visited:
                return
            visited.add(id(scope))
            for _, source in scope.selected_sources.values():
                if isinstance(source, Scope):
                    visit(source)
                elif isinstance(source, exp.Table):
                    if source.catalog or source.db.lower() not in ("", "main") or not isinstance(source.this, exp.Identifier):
                        raise ValueError("Unsupported physical source")
                    tables.add(source.name.lower())
                else:
                    raise ValueError("Unsupported source")
            for child in [*scope.subquery_scopes, *scope.union_scopes]:
                visit(child)

        visit(root)
        aggregates = {"agg_daily_sales", "agg_monthly_sales"}
        facts = {"orders", "lineitem"}
        dimensions = {"customer", "supplier", "nation", "region"}
        if not tables or tables - aggregates - facts - dimensions:
            return None
        if tables & aggregates and tables & facts:
            return None
        return "aggregated" if tables & aggregates else "fact" if tables & facts else "dimension"
    except (sqlglot.errors.SqlglotError, ValueError, TypeError, AttributeError):
        return None


def _flag(state, name, expected):
    value = state.get(name)
    return isinstance(value, (bool, np.bool_)) and bool(value) == expected


def score_result(final_state, test_case, golden=None):
    """Independently score correctness, declared verification, and overall delivery."""
    state = final_state if isinstance(final_state, dict) else {}
    score = {name: test_case.get(name, "") for name in ("id", "question", "failure_mode", "notes")}
    reasons = []
    if test_case["failure_mode"] == "out_of_range":
        sql = state.get("sql")
        passed = (
            _flag(state, "out_of_range", True)
            and "data" in state and state["data"] is None
            and _flag(state, "success", False) and _flag(state, "valid", False)
            and isinstance(sql, str) and sql.startswith("OUT_OF_RANGE:")
            and bool(sql.partition(":")[2].strip())
        )
        score.update(result_match=None, terminal_verified=None, layer_match=None,
                     out_of_range_match=bool(passed), passed=bool(passed))
        if not passed:
            reasons.append("Invalid out-of-range terminal state")
    else:
        try:
            rows = validate_contract(test_case, load_golden() if golden is None else golden)
            result_match, reasons = compare_result(state.get("data"), test_case["result"], rows)
        except ContractError as exc:
            result_match, reasons = False, [str(exc)]
        verified = _flag(state, "success", True) and _flag(state, "valid", True) and _flag(state, "out_of_range", False)
        layer = evaluation_layer(state.get("sql"))
        layer_match = test_case.get("expected_layer") in ("aggregated", "fact", "dimension") and layer == test_case["expected_layer"]
        if not verified:
            reasons.append("Terminal state is not explicitly verified and in range")
        if not layer_match:
            reasons.append(f"Layer mismatch: expected {test_case.get('expected_layer')}, detected {layer}")
        score.update(result_match=bool(result_match), terminal_verified=bool(verified),
                     layer_match=bool(layer_match), out_of_range_match=_flag(state, "out_of_range", False),
                     passed=bool(result_match and verified and layer_match))
    score["reasons"] = reasons
    return score
