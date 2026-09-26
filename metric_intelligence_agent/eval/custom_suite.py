"""Explicit, independently reviewed custom-source evaluation inputs."""
import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp

from agent import load_context
from eval.scoring import ContractError, load_golden, validate_contract
from layer_reporting import load_table_layers


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fields(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ContractError(f"Expected fields {required}; optional {optional}")


def text(value):
    if not isinstance(value, str) or not value.strip():
        raise ContractError("Expected nonempty text")


def load_suite(path, *, candidate=False):
    suite = load_golden(path)
    try:
        fields(suite, ("version", "source_id", "cases"), ("approval", "provenance"))
        if type(suite["version"]) is not int or suite["version"] != 1:
            raise ContractError("Unsupported custom-suite version")
        text(suite["source_id"])
        if not isinstance(suite["cases"], list) or not suite["cases"]:
            raise ContractError("Suite needs at least one case")
        ids, questions = set(), set()
        for case in suite["cases"]:
            fields(case, ("id", "question", "result", "reference", "metric", "tolerance_rationale", "notes"),
                   ("expected_rows", "expected_layer"))
            for name in ("id", "question", "tolerance_rationale", "notes"):
                text(case[name])
            question = " ".join(case["question"].casefold().split())
            if case["id"] in ids or question in questions:
                raise ContractError("Duplicate case ID or question")
            ids.add(case["id"])
            questions.add(question)
            if "expected_layer" in case and case["expected_layer"] not in ("aggregated", "fact", "dimension"):
                raise ContractError("Invalid expected_layer")
            fields(case["metric"], ("definition", "formula", "exclusions", "grain"))
            for name in ("definition", "formula", "exclusions"):
                text(case["metric"][name])
            contract = case["result"]
            fields(contract, ("keys", "values"), ("exact_schema",))
            if not isinstance(contract["keys"], dict) or not isinstance(contract["values"], dict):
                raise ContractError("keys/values must be objects")
            if case["metric"]["grain"] != list(contract["keys"]):
                raise ContractError("Metric grain must equal result keys in declared order")
            sample = {}
            for name, spec in {**contract["keys"], **contract["values"]}.items():
                fields(spec, ("type", "abs_tol", "rel_tol") if name in contract["values"] else ("type",), ("aliases",))
                sample[name] = {"text": "sample", "date": "2000-01-01"}.get(spec["type"], 1)
            # Reuse all Batch 1 type, alias, tolerance and expected-key validation.
            validate_contract({"result": {**contract, "fixture": case["id"]}}, {case["id"]: [sample]})
            reference = case["reference"]
            if reference.get("kind") == "sql":
                fields(reference, ("kind", "sql"))
                text(reference["sql"])
                statements = sqlglot.parse(reference["sql"], read="duckdb")
                if len(statements) != 1 or not isinstance(statements[0], exp.Query):
                    raise ContractError("Reference must be one read-only SELECT query")
            elif reference.get("kind") == "manual":
                fields(reference, ("kind", "note"))
                text(reference["note"])
            else:
                raise ContractError("Reference kind must be sql or manual")
            if not candidate or "expected_rows" in case or reference["kind"] == "manual":
                validate_case(case)
        if not candidate:
            fields(suite.get("approval"), ("status", "reviewer", "note"))
            if suite["approval"]["status"] != "approved":
                raise ContractError("Owner review required: suite is not approved")
            text(suite["approval"]["reviewer"])
            text(suite["approval"]["note"])
        return suite
    except (KeyError, TypeError, AttributeError, ValueError, sqlglot.errors.SqlglotError) as exc:
        raise ContractError(f"Invalid custom suite: {exc}") from exc


def validate_case(case):
    return validate_contract({"result": {**case["result"], "fixture": case["id"]}},
                             {case["id"]: case["expected_rows"]})


def inputs(db, context_dir, suite_path, *, candidate=False, check_drift=False):
    db, context_dir = Path(db), Path(context_dir)
    if not db.is_file():
        raise ContractError(f"Database file missing: {db}")
    if Path(str(db) + ".wal").exists():
        raise ContractError("Database has a WAL; close the writer and checkpoint before evaluation")
    if not context_dir.is_dir():
        raise ContractError(f"Context directory missing: {context_dir}")
    suite = load_suite(suite_path, candidate=candidate)
    context = load_context(context_dir=context_dir)  # Explicit custom context, no date fallback.
    layer_path = context_dir / "layer_classifications.json"
    layers = load_table_layers(context_dir)
    if not layer_path.is_file() or not isinstance(layers, dict) or any(
        not isinstance(k, str) or not k or v not in ("Aggregated", "Fact", "Dimension", "Bridge", "Skip",
                                                    "aggregated", "fact", "dimension", "bridge", "skip")
        for k, v in layers.items()
    ):
        raise ContractError("Valid analyst layer_classifications.json is required")
    metric_file = "metric_definitions.yaml" if (context_dir / "metric_definitions.yaml").is_file() else "metric_definitions.md"
    context_hashes = {name: file_digest(context_dir / name) for name in
                      ("table_catalog.md", "schema.sql", metric_file, "layer_classifications.json")}
    definitions = [{k: v for k, v in case.items() if k != "expected_rows"} for case in suite["cases"]]
    provenance = {
        "source_id": suite["source_id"], "database_sha256": file_digest(db),
        "context_sha256": digest(context_hashes), "context_files": context_hashes,
        "suite_definition_sha256": digest({"version": suite["version"], "source_id": suite["source_id"], "cases": definitions}),
        "duckdb_version": duckdb.__version__,
    }
    if not candidate:
        provenance["expected_sha256"] = digest({c["id"]: c["expected_rows"] for c in suite["cases"]})
        if suite.get("provenance") != provenance and not check_drift:
            raise ContractError("Golden provenance drift: DB/context/suite/expected rows/DuckDB version changed; generate and review a new candidate")
    return suite, context, layers, provenance


def parser(description):
    result = argparse.ArgumentParser(description=description)
    result.add_argument("--db", required=True, type=Path)
    result.add_argument("--context-dir", required=True, type=Path)
    result.add_argument("--suite", required=True, type=Path)
    return result


def write_new(path, document):
    """Never overwrite reviewed inputs or an earlier run report."""
    serialized = json.dumps(document, indent=2, allow_nan=False) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(serialized)
