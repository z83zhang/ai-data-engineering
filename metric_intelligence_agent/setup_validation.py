"""Persistence and deterministic checks for custom-source setup validation."""

import hashlib
import json
import math
import shutil
from datetime import datetime
from pathlib import Path


ROW_COUNT_COLUMN = "__row_count__"
MAX_VALIDATION_QUESTIONS = 5


def source_key(source_path):
    normalized = str(Path(source_path).resolve()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def validation_path(context_dir, source_path):
    return Path(context_dir) / "validation" / f"{source_key(source_path)}.json"


def empty_validation(source_path):
    return {
        "version": 1,
        "source_path": str(Path(source_path).resolve()),
        "questions": [],
        "ready": False,
    }


def load_validation(context_dir, source_path):
    path = validation_path(context_dir, source_path)
    if not path.is_file():
        return empty_validation(source_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    if Path(document.get("source_path", "")).resolve() != Path(source_path).resolve():
        return empty_validation(source_path)
    document.setdefault("questions", [])
    document.setdefault("ready", False)
    return document


def save_validation(context_dir, source_path, document, *, backup=True):
    path = validation_path(context_dir, source_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.is_file():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        shutil.copy2(path, path.with_suffix(f".{timestamp}.bak"))
    payload = dict(document)
    payload["version"] = 1
    payload["source_path"] = str(Path(source_path).resolve())
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def invalidate_readiness(context_dir, source_path):
    if not source_path:
        return
    document = load_validation(context_dir, source_path)
    if document.get("ready"):
        document["ready"] = False
        document["stale"] = True
        save_validation(context_dir, source_path, document, backup=False)


def deterministic_suggestion(data, expected_column, expected_value):
    """Compare one numeric value, or the row count, using a fixed basic tolerance."""
    if expected_value in (None, ""):
        return None, "No automated check configured."
    try:
        expected = float(expected_value)
    except (TypeError, ValueError):
        return False, "Expected value must be a number."

    if expected_column == ROW_COUNT_COLUMN:
        actual = len(data)
    else:
        if not expected_column:
            return False, "Choose the expected result column."
        if expected_column not in data.columns:
            return False, f"Column '{expected_column}' was not returned."
        if len(data) != 1:
            return False, "The automated check requires exactly one result row."
        actual = data.iloc[0][expected_column]
    try:
        actual_number = float(actual)
    except (TypeError, ValueError):
        return False, f"Actual value {actual!r} is not numeric."
    passed = math.isclose(actual_number, expected, rel_tol=1e-6, abs_tol=1e-9)
    comparison = "matches" if passed else "does not match"
    return passed, f"Actual {actual_number:g} {comparison} expected {expected:g}."


def validation_ready(document):
    questions = document.get("questions", [])
    results = document.get("results", {})
    return bool(questions) and all(
        results.get(question["id"], {}).get("analyst_decision") == "pass"
        and results.get(question["id"], {}).get("system_success")
        for question in questions
    )
