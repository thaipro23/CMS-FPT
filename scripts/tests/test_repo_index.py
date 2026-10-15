"""Exercise the index CLI against real temporary Git repositories (stdlib only)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "repo_index.py"


class RepoIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = dict(os.environ, GIT_AUTHOR_NAME="Index Test", GIT_AUTHOR_EMAIL="index@example.invalid",
                        GIT_COMMITTER_NAME="Index Test", GIT_COMMITTER_EMAIL="index@example.invalid")
        self.git("init", "-q")
        self.write("scripts/repo_index.py", SOURCE.read_text(encoding="utf-8"))
        self.write("docs/index-scope.json", json.dumps({"focus": ["custom/", "docs/", "scripts/"]}))
        self.write("custom/teacher_report.py", "report = 1\n")
        self.write("custom/nested/quiz.py", "quiz = 1\n")
        self.write("docs/CURRENT_STATE.md", "# Current\n")
        self.write("docs/history/old.md", "# Old\n")
        self.write("docs/superpowers/plans/proposal.md", "# Proposal\n")
        self.write("RELEASE_v1.md", "# Release\n")
        self.write("upstream/teacher_report.py", "legacy = 1\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.cli("build")

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], env=self.env,
                              capture_output=True, check=True).stdout.decode().strip()

    def cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(self.root / "scripts/repo_index.py"), *args],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_file_lookup_focus_and_escape_hatch(self):
        result = self.cli("find", "teacher report")
        self.assertEqual([x["path"] for x in result["results"]], ["custom/teacher_report.py"])
        self.assertEqual(self.cli("find", "teacher report", "--all")["total"], 2)
        self.assertEqual(self.cli("find", "teacher", "--prefix", "upstream")["total"], 1)

    def test_docs_history_and_proposals_are_opt_in(self):
        result = self.cli("find", "", "--docs")
        self.assertEqual([x["path"] for x in result["results"]], ["docs/CURRENT_STATE.md"])
        self.assertEqual(self.cli("find", "", "--docs", "--history")["total"], 3)
        self.assertEqual(self.cli("find", "", "--docs", "--all")["total"], 4)

    def test_directory_listing_and_bounded_results(self):
        result = self.cli("ls", "custom", "--limit", "1")
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["results"]), 1)
        self.assertTrue(result["truncated"])
        nested = self.cli("ls", "custom/nested")
        self.assertEqual(nested["results"][0]["path"], "custom/nested/quiz.py")

    def test_root_listing_prioritizes_focused_directories(self):
        result = self.cli("ls")
        self.assertEqual([row["path"] for row in result["results"]],
                         ["custom/", "docs/", "scripts/"])
        all_paths = [row["path"] for row in self.cli("ls", "--all")["results"]]
        self.assertIn("upstream/", all_paths)
        self.assertIn("RELEASE_v1.md", all_paths)

    def test_generated_catalog_does_not_invalidate_itself(self):
        self.git("add", "docs/_index/catalog.json")
        self.git("commit", "-qm", "index only")
        result = self.cli("check")
        self.assertTrue(result["fresh"])
        self.assertEqual(result["dirty"]["total"], 0)

    def test_executable_mode_change_is_detected(self):
        self.git("update-index", "--chmod=+x", "custom/teacher_report.py")
        self.git("commit", "-qm", "change executable mode")
        result = self.cli("check", expected=1)
        self.assertEqual(result["modified"]["results"], ["custom/teacher_report.py"])

    def test_rename_in_dirty_worktree_preserves_both_paths(self):
        self.git("mv", "custom/teacher_report.py", "custom/name with spaces.py")
        result = self.cli("check", expected=1)
        self.assertEqual(result["dirty"]["results"],
                         ["custom/name with spaces.py", "custom/teacher_report.py"])

    def test_committed_add_modify_rename_delete_are_detected(self):
        self.write("custom/teacher_report.py", "report = 2\n")
        self.git("mv", "custom/nested/quiz.py", "custom/nested/final.py")
        self.write("custom/new.bin", "binary fixture\n")
        self.git("add", "custom")
        self.git("commit", "-qm", "change source")
        result = self.cli("check", expected=1)
        self.assertFalse(result["fresh"])
        self.assertEqual(result["added"]["total"], 2)
        self.assertEqual(result["removed"]["total"], 1)
        self.assertEqual(result["modified"]["total"], 1)
        self.cli("build")
        self.assertTrue(self.cli("check")["fresh"])

    def test_dirty_and_staged_changes_are_reported_build_refuses(self):
        self.write("custom/teacher_report.py", "dirty = 1\n")
        self.write("custom/untracked.py", "new = 1\n")
        result = self.cli("check", expected=1)
        self.assertTrue(result["fresh"])
        self.assertEqual(result["dirty"]["total"], 2)
        self.assertIn("committed", self.cli("build", expected=2)["error"])
        self.git("add", "custom")
        self.assertEqual(self.cli("check", expected=1)["dirty"]["total"], 2)

    def test_catalog_corruption_is_rejected(self):
        path = self.root / "docs/_index/catalog.json"
        data = json.loads(path.read_text())
        data["entries"][0][1] = "0" * 40
        path.write_text(json.dumps(data))
        result = self.cli("find", "teacher", expected=2)
        self.assertIn("fingerprint", result["error"])

    def test_symlink_target_is_never_opened_and_paths_with_spaces_work(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlink not supported")
        (self.root / "custom/outside_link").symlink_to("/nonexistent/private-file")
        self.write("custom/name with spaces.py", "value = 1\n")
        self.git("add", "custom")
        self.git("commit", "-qm", "link and space")
        self.cli("build")
        self.assertEqual(self.cli("find", "outside_link")["results"][0]["mode"], "120000")
        self.assertEqual(self.cli("find", "name with spaces")["total"], 1)

    def test_queries_work_without_git_but_check_reports_missing_git(self):
        shutil.rmtree(self.root / ".git")
        self.assertEqual(self.cli("find", "teacher")["total"], 1)
        self.assertIn("Git", self.cli("check", expected=2)["error"])


if __name__ == "__main__":
    unittest.main()
