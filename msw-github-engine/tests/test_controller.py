"""Security and execution regressions using isolated, real Git repositories."""
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("msw_github_controller", SOURCE / "controller.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "policy.json"
        shutil.copyfile(SOURCE / "policy.json", self.path)
        self.now = datetime.fromisoformat("2026-11-10T12:00:00+01:00")

    def test_active_and_exact_window_edges(self):
        self.assertTrue(c.gate(self.path, self.now, {})["enabled"])
        self.assertTrue(c.gate(self.path, datetime.fromisoformat("2026-11-06T23:00:00+00:00"), {})["enabled"])
        self.assertEqual(c.gate(self.path, datetime.fromisoformat("2026-12-06T23:00:00+00:00"), {})["reason"], "expired")
        self.assertEqual(c.gate(self.path, datetime.fromisoformat("2026-11-06T22:59:59+00:00"), {})["reason"], "not_started")

    def test_environment_stop_and_invalid_override(self):
        for value in ("0", "false", "FALSE"):
            self.assertEqual(c.gate(self.path, self.now, {"MSW_GITHUB_ENGINE_ENABLED": value})["reason"], "disabled")
        self.assertFalse(c.gate(self.path, self.now, {"MSW_GITHUB_ENGINE_ENABLED": "yes"})["enabled"])

    def test_missing_malformed_and_duplicate_policy_fail_closed(self):
        self.assertFalse(c.gate(self.path.with_name("missing.json"), self.now, {})["enabled"])
        for raw in ("not json", '{"enabled":true,"enabled":false}', "[]"):
            self.path.write_text(raw)
            self.assertEqual(c.gate(self.path, self.now, {})["reason"], "policy_invalid")

    def test_invalid_policy_scope_and_unbounded_limit(self):
        original = json.loads(self.path.read_text())
        for changes in ({"max_files": 13}, {"max_bytes": 204801}, {"enabled": "true"},
                        {"active_until": "2026-12-07"}, {"allowed_prefixes": [""]}):
            self.path.write_text(json.dumps({**original, **changes}))
            self.assertFalse(c.gate(self.path, self.now, {})["enabled"])

    def test_policy_disabled_and_naive_clock_fail_closed(self):
        policy = json.loads(self.path.read_text())
        policy["enabled"] = False
        self.path.write_text(json.dumps(policy))
        self.assertEqual(c.gate(self.path, self.now, {})["reason"], "disabled")
        self.assertFalse(c.gate(self.path, datetime(2026, 11, 10), {})["enabled"])


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Controller tests")
        self.git("config", "user.email", "fixture@example.invalid")
        self.write("msw-ai-technical-core/server.mjs", "export const answer = 1;\n")
        self.write("msw-ai-technical-core/tests/smoke.test.mjs", "// immutable original test\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline fixture")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.policy = c.load_policy(SOURCE / "policy.json")

    def git(self, *args):
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(self.root), *args], capture_output=True, text=True, check=True)
        return result.stdout

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    def inspect(self):
        return c.inspect_changes(self.root, self.base, self.policy)

    def test_clean_diff_does_not_claim_tests_passed(self):
        self.assertEqual(self.inspect(), [])
        with patch.object(c, "run_checks", side_effect=AssertionError("must not run")):
            report = c.evaluate(self.root, self.base, self.policy)
        self.assertEqual(report["status"], "NO_CHANGES")
        self.assertEqual(report["tests"], [])

    def test_modified_code_and_untracked_new_file_included(self):
        self.write("msw-ai-technical-core/server.mjs", "export const answer = 2;\n")
        self.write("msw-ai-technical-core/new.mjs", "export const helper = true;\n")
        result = self.inspect()
        self.assertEqual({x["change"] for x in result}, {"modified", "added"})
        self.assertTrue(all(len(x["sha256"]) == 64 for x in result))

    def test_protected_paths_and_runtime_data(self):
        for path in (".github/workflows/new.yml", "msw-github-engine/controller.py", "msw-ai-technical-core/render.yaml",
                     "msw-ai-technical-core/package.json", "msw-research-engine/data/new.json", "msw-ai-technical-core/.env",
                     "msw-ai-technical-core/deploy.py"):
            with self.subTest(path=path):
                target = self.write(path, "{}\n")
                with self.assertRaises(c.Blocked):
                    self.inspect()
                target.unlink()

    def test_existing_test_edit_delete_and_rename_blocked(self):
        path = "msw-ai-technical-core/tests/smoke.test.mjs"
        target = self.root / path
        original = target.read_text()
        target.write_text("// removed every assertion\n")
        with self.assertRaisesRegex(c.Blocked, "existing_test"):
            self.inspect()
        target.unlink()
        with self.assertRaisesRegex(c.Blocked, "existing_test"):
            self.inspect()
        renamed = self.write("msw-ai-technical-core/tests/renamed.test.mjs", original)
        with self.assertRaisesRegex(c.Blocked, "existing_test"):
            self.inspect()
        renamed.unlink()
        target.write_text(original)

    def test_symlinks_and_hardlinks_blocked(self):
        link = self.root / "msw-ai-technical-core/link.mjs"
        link.symlink_to(self.root / "msw-ai-technical-core/server.mjs")
        with self.assertRaisesRegex(c.Blocked, "symlink"):
            self.inspect()
        link.unlink()
        os.link(self.root / "msw-ai-technical-core/server.mjs", link)
        with self.assertRaisesRegex(c.Blocked, "nonregular"):
            self.inspect()

    def test_gitlink_blocked(self):
        self.git("update-index", "--add", "--cacheinfo", "160000," + self.base + ",msw-ai-technical-core/submodule")
        with self.assertRaises(c.Blocked):
            self.inspect()

    def test_staged_content_must_match_verified_worktree(self):
        path = "msw-ai-technical-core/server.mjs"
        self.write(path, "export const answer = 2;\n")
        self.git("add", path)
        self.assertEqual(len(self.inspect()), 1)
        self.write(path, "export const answer = 3;\n")
        with self.assertRaisesRegex(c.Blocked, "index_worktree_mismatch"):
            self.inspect()

    def test_staged_cache_payload_cannot_hide_beside_allowed_edit(self):
        path = "msw-ai-technical-core/__pycache__/payload.py"
        self.write(path, "print('unreviewed payload')\n")
        self.git("add", path)
        self.write("msw-ai-technical-core/server.mjs", "export const answer = 2;\n")
        with self.assertRaisesRegex(c.Blocked, "protected_path"):
            self.inspect()

    def test_committed_cache_payload_cannot_hide_beside_allowed_edit(self):
        path = "msw-ai-technical-core/__pycache__/payload.py"
        self.write(path, "print('unreviewed payload')\n")
        self.git("add", path)
        self.git("commit", "-qm", "candidate contains forbidden cache payload")
        self.write("msw-ai-technical-core/server.mjs", "export const answer = 2;\n")
        with self.assertRaisesRegex(c.Blocked, "protected_path"):
            self.inspect()

    def test_staged_report_cannot_hide_beside_allowed_edit(self):
        path = ".msw-github-engine/forged-receipt.json"
        self.write(path, '{"status":"PASS"}\n')
        self.git("add", path)
        self.write("msw-ai-technical-core/server.mjs", "export const answer = 2;\n")
        with self.assertRaises(c.Blocked):
            self.inspect()

    def test_file_and_byte_limits(self):
        source = self.write("msw-ai-technical-core/big.mjs", "x" * 204801)
        with self.assertRaisesRegex(c.Blocked, "byte_limit"):
            self.inspect()
        source.unlink()
        for number in range(13):
            self.write("msw-ai-technical-core/file" + str(number) + ".mjs", "// x\n")
        with self.assertRaisesRegex(c.Blocked, "file_limit"):
            self.inspect()

    def test_secret_markers_blocked_but_synthetic_test_token_allowed(self):
        source = self.write("msw-ai-technical-core/new.mjs", "const key = 'test-fixture-not-a-production-credential-123456';\n")
        self.assertEqual(len(self.inspect()), 1)
        for secret in ("sk-proj-" + "a" * 40, "ghp_" + "b" * 36, "-----BEGIN PRIVATE KEY-----"):
            source.write_text("const key = '" + secret + "';\n")
            with self.assertRaisesRegex(c.Blocked, "secret"):
                self.inspect()

    def test_new_tests_selected_for_actual_execution(self):
        self.write("msw-ai-technical-core/tests/regression.test.mjs", "// new test\n")
        self.write("msw-research-engine/tests/test_regression.py", "# new test\n")
        self.write("msw-ai-centrum-robotow/tests/test_new_api.py", "# new test\n")
        checks = c.select_checks(self.inspect())
        self.assertIn("new:msw-ai-technical-core/tests/regression.test.mjs", checks)
        self.assertIn("new:msw-research-engine/tests/test_regression.py", checks)
        self.assertIn("new:msw-ai-centrum-robotow/tests/test_new_api.py", checks)
        self.assertIn("portal-api", checks)
        self.assertTrue(all(x["minimum"] >= 1 for x in checks.values()))

    def test_base_snapshot_preserves_original_tests_and_overlays_candidate(self):
        self.write("msw-ai-technical-core/server.mjs", "export const answer = 2;\n")
        changes = self.inspect()
        def observe(name, spec, snapshot):
            self.assertNotEqual(snapshot, self.root)
            self.assertEqual((snapshot / "msw-ai-technical-core/tests/smoke.test.mjs").read_text(), "// immutable original test\n")
            self.assertIn("answer = 2", (snapshot / "msw-ai-technical-core/server.mjs").read_text())
            return {"id": name, "status": "PASS", "checks": 9}
        with patch.object(c, "run_check", side_effect=observe):
            report = c.evaluate(self.root, self.base, self.policy)
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["production_verified"])

    def test_changes_during_validation_are_detected_by_content_digest(self):
        self.write("msw-ai-technical-core/server.mjs", "export const answer = 2;\n")
        def change(*args):
            self.write("msw-ai-technical-core/server.mjs", "export const answer = 3;\n")
            return [{"status": "PASS"}]
        with patch.object(c, "run_checks", side_effect=change):
            with self.assertRaisesRegex(c.Blocked, "changed_during"):
                c.evaluate(self.root, self.base, self.policy)

    def test_empty_evidence_and_failure_never_become_success(self):
        self.write("msw-ai-technical-core/server.mjs", "export const answer = 2;\n")
        with patch.object(c, "run_checks", return_value=[]):
            with self.assertRaisesRegex(c.Blocked, "no_test_evidence"):
                c.evaluate(self.root, self.base, self.policy)
        with patch.object(c, "run_checks", return_value=[{"status": "FAIL", "exit_code": 1}]):
            self.assertEqual(c.evaluate(self.root, self.base, self.policy)["status"], "FAIL")

    def test_report_symlink_rejected(self):
        (self.root / c.REPORT_DIR).symlink_to(self.root / "msw-ai-technical-core", target_is_directory=True)
        with self.assertRaisesRegex(c.Blocked, "report_directory_symlink"):
            c.save_report(self.root, "validate", {"status": "PASS"})


class ExecutionTests(unittest.TestCase):
    def test_counts_require_real_reported_execution(self):
        self.assertEqual(c.observed_count("node", "# tests 9\n# pass 9\n"), 9)
        self.assertEqual(c.observed_count("unittest", "Ran 5 tests in 0.01s\nOK\n"), 5)
        self.assertEqual(c.observed_count("unittest", "Ran 5 tests in 0.01s\nOK (skipped=5)\n"), 0)
        self.assertEqual(c.observed_count("node", "# tests 9\n# pass 0\n# skipped 9\n"), 0)
        self.assertEqual(c.observed_count("pytest", "9 passed in 0.5s\n"), 9)
        self.assertEqual(c.observed_count("pytest", "9 skipped in 0.5s\n"), 0)
        self.assertEqual(c.observed_count("integration", '{"schema":"msw.compute.integration-test.v1","passed":false,"checks":12}'), 0)

    def test_missing_pytest_dependencies_block_instead_of_skip(self):
        with patch.object(c.importlib.util, "find_spec", return_value=None):
            result = c.run_check("portal-api", c.REGISTRY["portal-api"], Path("."))
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("pytest", result["missing"])

    def test_subprocess_failure_and_zero_test_execution_fail(self):
        for code, raw in ((1, b"# tests 9\n# pass 9\n"), (0, b"# tests 0\n# pass 0\n"), (0, b"no tests\n")):
            def fake(command, **kwargs):
                self.assertFalse(kwargs["shell"])
                self.assertEqual(kwargs["timeout"], 180)
                self.assertNotIn("GITHUB_TOKEN", kwargs["env"])
                self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
                kwargs["stdout"].write(raw)
                return subprocess.CompletedProcess(command, code)
            with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"GITHUB_TOKEN": "synthetic-token", "OPENAI_API_KEY": "synthetic-key"}), patch.object(c.subprocess, "run", side_effect=fake):
                result = c.run_check("technical-core", c.REGISTRY["technical-core"], Path(directory))
            self.assertEqual(result["status"], "FAIL")
            self.assertIn("output_tail", result)

    def test_failure_output_is_bounded_and_redacts_key_patterns(self):
        tail = c.sanitized_tail("x" * 5000 + "\n" + "sk-proj-" + "a" * 40 + "\npassword=synthetic-secret\nAuthorization: Bearer synthetic-bearer")
        self.assertLessEqual(len(tail), 4000)
        self.assertNotIn("sk-proj-", tail)
        self.assertNotIn("synthetic-secret", tail)
        self.assertNotIn("synthetic-bearer", tail)

    def test_test_timeout_is_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(c.subprocess, "run", side_effect=subprocess.TimeoutExpired("node", 180)):
            result = c.run_check("technical-core", c.REGISTRY["technical-core"], Path(directory))
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "test_timeout")


if __name__ == "__main__":
    unittest.main()
