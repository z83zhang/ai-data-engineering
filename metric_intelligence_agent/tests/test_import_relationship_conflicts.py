import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "test-only-placeholder")

from app_pages.setup import _save_sql_metric_document
from metric_import import (
    RelationshipConflictError,
    load_metric_definitions_yaml,
    render_metric_definitions_yaml,
    save_metric_definitions_yaml,
)
from tests.test_metric_yaml import worked_example


class ImportRelationshipConflictTests(unittest.TestCase):
    def test_import_save_requires_confirmation_before_relationship_change(self):
        existing = worked_example()
        incoming = deepcopy(existing)
        incoming["entities"]["paid_orders"]["keys"][1][
            "references_entity"
        ] = "customer_totals"

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "metric_definitions.yaml"
            save_metric_definitions_yaml(path, existing)
            reviewed = render_metric_definitions_yaml(incoming)

            with self.assertRaises(RelationshipConflictError) as raised:
                _save_sql_metric_document(path, reviewed, incoming)

            self.assertEqual(len(raised.exception.conflicts), 1)
            self.assertEqual(load_metric_definitions_yaml(path), existing)

            _save_sql_metric_document(
                path,
                reviewed,
                incoming,
                confirm_relationship_conflicts=True,
            )
            saved = load_metric_definitions_yaml(path)

        self.assertEqual(
            saved["entities"]["paid_orders"]["keys"][1][
                "references_entity"
            ],
            "customer_totals",
        )


if __name__ == "__main__":
    unittest.main()
