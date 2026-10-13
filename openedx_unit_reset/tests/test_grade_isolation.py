import ast
import importlib
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SERVICES_PATH = PACKAGE_ROOT / "openedx_unit_reset" / "services.py"
TASKS_PATH = PACKAGE_ROOT / "openedx_unit_reset" / "tasks.py"
MODELS_PATH = PACKAGE_ROOT / "openedx_unit_reset" / "models.py"
ENROLLMENT_PATH = PACKAGE_ROOT.parent / "lms" / "djangoapps" / "instructor" / "enrollment.py"


class UnitResetScopeTests(unittest.TestCase):
    def test_dynamic_itembank_scope_keeps_only_selected_children(self):
        try:
            reset_scope = importlib.import_module("openedx_unit_reset.reset_scope")
        except ModuleNotFoundError:
            self.fail("openedx_unit_reset.reset_scope must define isolated reset targeting")

        selected = "block-v1:FPL+COURSE+RUN+type@problem+block@selected"
        unselected = "block-v1:FPL+COURSE+RUN+type@problem+block@unselected"

        children = reset_scope.active_child_keys(
            structural_children=[selected, unselected],
            selected_children=[selected],
            dynamic=True,
        )

        self.assertEqual(children, [selected])
        self.assertNotIn(unselected, children)

    def test_only_leaf_state_uses_official_reset_api(self):
        reset_scope = importlib.import_module("openedx_unit_reset.reset_scope")
        problem = "block-v1:FPL+COURSE+RUN+type@problem+block@selected"
        unit = "block-v1:FPL+COURSE+RUN+type@vertical+block@quiz"
        item_bank = "block-v1:FPL+COURSE+RUN+type@itembank+block@bank"

        official, containers = reset_scope.partition_persisted_reset_keys(
            persisted_keys={problem, unit, item_bank},
            container_keys={unit, item_bank},
        )

        self.assertEqual(official, [problem])
        self.assertEqual(containers, sorted([unit, item_bank]))

    def test_reset_path_queues_one_post_commit_reconciliation(self):
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
        self.assertIn("transaction.on_commit", reset_source)
        self.assertIn("grade_reconciliation", reset_source)

        # These direct model deletions remove grades outside the requested Unit
        # and bypass Open edX's score-reset signals. They must not return.
        self.assertNotIn("def clear_user_grade_cache", source)
        self.assertNotIn("def clear_user_submissions", source)
        self.assertIn("reset_student_attempts", source)
        self.assertIn("emit_signals_and_events=False", source)

        reset_state_function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "reset_unit_attempt_state"
        )
        reset_state_source = ast.get_source_segment(source, reset_state_function)
        self.assertNotIn("module_state_key__in=list(reset_keys),\n    ).delete()", reset_state_source)

    def test_itembank_scope_reads_only_exact_selected_field(self):
        source = SERVICES_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        extract_function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "extract_selected_usage_keys_from_state"
        )
        extract_source = ast.get_source_segment(source, extract_function)

        self.assertIn('data.get("selected", [])', extract_source)
        self.assertNotIn("def scan", extract_source)
        self.assertNotIn("UsageKey.from_string", extract_source)

    def test_grade_reconciliation_has_durable_retry_and_failure_status(self):
        task_source = TASKS_PATH.read_text(encoding="utf-8")
        model_source = MODELS_PATH.read_text(encoding="utf-8")

        self.assertIn("max_retries=2", task_source)
        self.assertIn("acks_late=True", task_source)
        self.assertIn("reject_on_worker_lost=True", task_source)
        self.assertIn("STATUS_RETRYING", task_source)
        self.assertIn("STATUS_FAILED", task_source)
        self.assertIn("force_update_subsections=True", task_source)
        self.assertIn("class UnitResetGradeReconciliation", model_source)
        self.assertIn('STATUS_PENDING = "PENDING"', model_source)
        self.assertIn('STATUS_COMPLETED = "COMPLETED"', model_source)

    def test_core_recursive_reset_propagates_bulk_signal_policy(self):
        source = ENROLLMENT_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        reset_function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "reset_student_attempts"
        )
        reset_source = ast.get_source_segment(source, reset_function)

        self.assertIn("emit_signals_and_events=emit_signals_and_events", reset_source)

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
