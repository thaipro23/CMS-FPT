import ast
import importlib
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SERVICES_PATH = PACKAGE_ROOT / "openedx_unit_reset" / "services.py"


class UnitResetScopeTests(unittest.TestCase):
    def test_official_reset_targets_exclude_unrelated_quiz(self):
        try:
            reset_scope = importlib.import_module("openedx_unit_reset.reset_scope")
        except ModuleNotFoundError:
            self.fail("openedx_unit_reset.reset_scope must define isolated reset targeting")

        quiz_1_problem = "block-v1:FPL+COURSE+RUN+type@problem+block@quiz-1"
        quiz_2_unit = "block-v1:FPL+COURSE+RUN+type@vertical+block@quiz-2"
        quiz_2_problem = "block-v1:FPL+COURSE+RUN+type@problem+block@quiz-2"
        quiz_2_randomized = "lb:library-v1:FPL+bank+type@problem+block@selected-2"

        targets = reset_scope.official_reset_targets(
            unit_key=quiz_2_unit,
            structural_keys={quiz_2_unit, quiz_2_problem},
            expanded_keys={quiz_2_unit, quiz_2_problem, quiz_2_randomized},
        )

        self.assertEqual(targets, [quiz_2_unit, quiz_2_randomized])
        self.assertNotIn(quiz_1_problem, targets)

    def test_reset_path_uses_openedx_grade_signals_not_coursewide_deletes(self):
        source = SERVICES_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        reset_function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "reset_unit_for_current_user"
        )
        reset_source = ast.get_source_segment(source, reset_function)

        self.assertNotIn("clear_user_grade_cache", reset_source)
        self.assertNotIn("clear_user_submissions", reset_source)
        self.assertIn("reset_unit_attempt_state", reset_source)
        self.assertIn("recalculate_user_course_grade", reset_source)

        # These direct model deletions remove grades outside the requested Unit
        # and bypass Open edX's score-reset signals. They must not return.
        self.assertNotIn("def clear_user_grade_cache", source)
        self.assertNotIn("def clear_user_submissions", source)
        self.assertIn("reset_student_attempts", source)
        self.assertIn("force_update_subsections=True", source)

    def test_api_rejects_sequence_key_as_unit_scope(self):
        source = SERVICES_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        parse_function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "parse_keys"
        )
        parse_source = ast.get_source_segment(source, parse_function)

        self.assertIn('block_type', parse_source)
        self.assertIn('"vertical"', parse_source)
        self.assertIn('"UNIT_KEY_NOT_VERTICAL"', parse_source)


if __name__ == "__main__":
    unittest.main()
