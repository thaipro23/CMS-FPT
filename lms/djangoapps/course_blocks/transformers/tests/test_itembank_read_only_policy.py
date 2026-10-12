"""Dependency-free tests for read-only ItemBank selection reporting."""

import importlib.util
from pathlib import Path
import unittest


MODULE = Path(__file__).resolve().parents[1] / "itembank_read_only.py"
TRANSFORMER = Path(__file__).resolve().parents[1] / "library_content.py"


class ItemBankReadOnlyPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("itembank_read_only_under_test", MODULE)
        cls.policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.policy)

    def test_missing_selection_is_reported_without_fabricating_random_children(self):
        valid = {
            ("problem", "question-1"),
            ("problem", "question-2"),
            ("problem", "question-3"),
        }

        result = self.policy.resolve_stored_selection([], valid, max_count=2)

        self.assertEqual(result.selected, [])
        self.assertEqual(result.missing_count, 2)
        self.assertTrue(result.requires_write)

    def test_valid_selection_is_preserved_without_requesting_a_write(self):
        selected = [["problem", "question-2"], ["problem", "question-1"]]
        valid = {
            ("problem", "question-1"),
            ("problem", "question-2"),
            ("problem", "question-3"),
        }

        result = self.policy.resolve_stored_selection(selected, valid, max_count=2)

        self.assertEqual(result.selected, selected)
        self.assertEqual(result.missing_count, 0)
        self.assertFalse(result.requires_write)

    def test_observation_scope_collects_mutations_and_restores_context(self):
        self.assertIsNone(self.policy.current_itembank_read_only_observation())

        with self.policy.observe_itembank_selections_read_only() as observation:
            observation.record("block-v1:test", reason="selection_missing", missing_count=2)
            self.assertIs(self.policy.current_itembank_read_only_observation(), observation)

        self.assertIsNone(self.policy.current_itembank_read_only_observation())
        self.assertTrue(observation.requires_write)
        self.assertEqual(observation.status, "selection_missing")
        self.assertEqual(observation.issues[0]["reason"], "selection_missing")

    def test_malformed_or_duplicate_selection_is_stale_not_an_exception(self):
        selected = [None, ["problem"], ["problem", "question-1"], ["problem", "question-1"]]
        valid = {("problem", "question-1")}

        result = self.policy.resolve_stored_selection(selected, valid, max_count=1)

        self.assertEqual(result.selected, [["problem", "question-1"]])
        self.assertTrue(result.invalid)
        self.assertTrue(result.requires_write)

        with self.policy.observe_itembank_selections_read_only() as observation:
            observation.record("block-v1:test", reason="selection_stale", invalid=result.invalid)
        self.assertEqual(observation.status, "selection_stale")

    def test_content_library_transformer_wires_read_only_policy_before_save(self):
        source = TRANSFORMER.read_text(encoding="utf-8")

        self.assertIn("current_itembank_read_only_observation", source)
        self.assertIn("resolve_stored_selection", source)
        self.assertIn("if read_only_observation is None:", source)
        self.assertIn("read_only_observation.record(", source)


if __name__ == "__main__":
    unittest.main()
