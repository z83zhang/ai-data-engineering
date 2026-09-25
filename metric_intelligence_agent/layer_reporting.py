"""Report physical layers using source-owned classifications, never name guesses."""
import json
import re
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope


def load_table_layers(context_dir=None, *, demo=False):
    if demo:
        # The bundled catalog is the demo's existing layer authority.
        catalog = Path(__file__).parent / "context" / "table_catalog.md"
        layers, layer = {}, None
        for line in catalog.read_text(encoding="utf-8").splitlines():
            section = re.fullmatch(r"## (Aggregated|Fact|Dimension) Layer", line)
            if section:
                layer = section[1].lower()
            table = re.fullmatch(r"### `([^`]+)`", line)
            if table and layer:
                layers[table[1]] = layer
        return layers
    if context_dir is None:
        return {}
    path = Path(context_dir) / "layer_classifications.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def detect_layer_used(sql, table_layers=None):
    """Highest known physical layer: aggregated > fact > dimension.

    Missing, ambiguous, Bridge/Skip, or unsupported references report unknown.
    Qualified references require qualified metadata (except DuckDB's main schema).
    """
    if not sql:
        return None
    mapping = {}
    for table, layer in (table_layers or {}).items():
        key = table.casefold()
        value = layer.casefold()
        mapping[key] = value if key not in mapping or mapping[key] == value else "unknown"
    try:
        statements = sqlglot.parse(sql, read="duckdb")
        if len(statements) != 1 or not isinstance(statements[0], exp.Query):
            return "unknown"
        layers = []
        for scope in traverse_scope(statements[0]):
            for _, source in scope.selected_sources.values():
                if not isinstance(source, exp.Table):
                    continue  # CTE/subquery; its physical sources have their own scope.
                if not isinstance(source.this, exp.Identifier):
                    return "unknown"
                key = ".".join(part.name for part in source.parts).casefold()
                layer = mapping.get(key)
                if layer is None and source.db.casefold() == "main" and not source.catalog:
                    layer = mapping.get(source.name.casefold())
                if layer not in ("aggregated", "fact", "dimension"):
                    return "unknown"
                layers.append(layer)
        return next((layer for layer in ("aggregated", "fact", "dimension") if layer in layers), "unknown")
    except (sqlglot.errors.SqlglotError, ValueError):
        return "unknown"
