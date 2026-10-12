"""Source contract for side-effect-free official progress reads."""

import ast
from pathlib import Path
import unittest


PLUGIN = Path(__file__).resolve().parents[1] / "openedx_ai_connector"
STUDENT_INSIGHT = PLUGIN / "student_insight.py"


class ItemBankReadOnlyProgressTests(unittest.TestCase):
    def test_official_progress_observes_itembank_selection_without_persisting_it(self):
        source = STUDENT_INSIGHT.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_completion_api_progress_snapshot"
        )
        function_source = ast.get_source_segment(source, function)

        self.assertIn("observe_itembank_selections_read_only", source)
        self.assertIn("observe_itembank_selections_read_only() as selection_observation:", function_source)
        self.assertIn("optional_reporting_dependency() as refused_sql", function_source)
        self.assertIn("'itembank_selection_status': selection_observation.status", function_source)
        self.assertIn("if selection_observation.requires_write:", function_source)

    def test_compact_progress_keeps_selection_status_for_dashcms(self):
        source = STUDENT_INSIGHT.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_compact_progress_payload"
        )
        function_source = ast.get_source_segment(source, function)

        self.assertIn("'itembank_selection_status'", function_source)
        self.assertIn("'itembank_selection_issue_count'", function_source)
        self.assertIn("'itembank_selection_status': progress.get('itembank_selection_status')", source)

    def test_component_grades_are_not_truncated_at_eighty(self):
        source = STUDENT_INSIGHT.read_text(encoding="utf-8")

        self.assertNotIn("usable[:80]", source)
        self.assertNotIn("existing[:80]", source)
        self.assertNotIn("planned_components[:80]", source)


if __name__ == "__main__":
    unittest.main()
