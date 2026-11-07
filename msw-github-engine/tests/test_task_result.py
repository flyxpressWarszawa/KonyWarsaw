"""Synthetic Git/HTTP evidence: no production requests, writes or model calls."""
import base64
from contextlib import redirect_stdout, redirect_stderr
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1]
ROOT = SOURCE.parent
spec = importlib.util.spec_from_file_location("msw_task_result", SOURCE / "task_result.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
TASK = r.bridge.decode_json((ROOT / "msw-coding-bridge/tests/fixtures/task-utf8.json").read_bytes())
PATH = "msw-ai-technical-core/engine.mjs"
TEST_PATH = "msw-ai-technical-core/tests/smoke.test.mjs"
TOKEN = "synthetic-read-token-not-a-production-credential"


def git(root, *args, raw=False):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, env=env)
    return result.stdout if raw else result.stdout.decode().strip()


class FakeHTTP:
    def __init__(self, fixture, proof):
        self.calls, self.failure, self.on_recheck = [], None, None
        self.pr_calls = 0
        repo = {"id": r.REPOSITORY_ID, "full_name": r.bridge.REPOSITORY}
        self.values = {
            "/pulls/17": {"number": 17, "state": "open", "merged": False, "merged_at": None,
                          "base": {"ref": "main", "sha": fixture.base, "repo": copy.deepcopy(repo)},
                          "head": {"ref": proof["branch"], "sha": proof["commit"], "repo": copy.deepcopy(repo)},
                          "body": "Proposed change.\n\n" + proof["pr_binding_block"] + "\n\nLocal evidence only.\n",
                          "changed_files": len(proof["changes"])},
            "/git/commits/" + proof["commit"]: {"sha": proof["commit"], "tree": {"sha": proof["tree"]},
                                                "parents": [{"sha": fixture.base}]},
            "/pulls/17/files?per_page=100&page=1": [],
        }
        for change in proof["changes"]:
            self.values["/pulls/17/files?per_page=100&page=1"].append({
                "filename": change["path"], "status": {"deleted": "removed"}.get(change["change"], change["change"]),
                "sha": change["git_blob_sha"]})
            raw = git(fixture.root, "cat-file", "blob", change["git_blob_sha"], raw=True)
            self.values["/git/blobs/" + change["git_blob_sha"]] = {
                "sha": change["git_blob_sha"], "encoding": "base64", "size": len(raw),
                "content": base64.b64encode(raw).decode("ascii")}

    def request(self, method, url, token, *, stage, max_bytes):
        assert method == "GET"
        assert url.startswith(r.bridge.API_ROOT + "/")
        assert token == TOKEN
        assert max_bytes == r.MAX_ARTIFACT
        suffix = url[len(r.bridge.API_ROOT):]
        self.calls.append((method, suffix, stage))
        if self.failure:
            raise self.failure
        if suffix == "/pulls/17":
            self.pr_calls += 1
            if self.pr_calls == 2 and self.on_recheck:
                self.on_recheck(self.values[suffix])
        return 200, copy.deepcopy(self.values[suffix])


class ResultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="msw-result-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.artifacts = Path(self.temp.name) / "artifacts"
        self.artifacts.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.name", "Synthetic verifier test")
        git(self.root, "config", "user.email", "fixture@example.invalid")
        self.write(PATH, "export const result = 1;\n")
        self.write(TEST_PATH, "// Existing immutable test fixture.\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "fixture base")
        self.base = git(self.root, "rev-parse", "HEAD")
        self.context = {
            "schema": r.task_context.SCHEMA, "status": "CONTEXT_READY", "repository": r.bridge.REPOSITORY,
            "queue_branch": r.bridge.QUEUE_BRANCH, "queue_commit": "1" * 40, "queue_head_sha": "2" * 40,
            "task_path": "msw-coding-tasks/" + TASK["id"] + ".json", "task_id": TASK["id"],
            "request_sha256": TASK["request_sha256"], "file_sha256": hashlib.sha256(r.bridge.validate_task(TASK)).hexdigest(),
            "task": copy.deepcopy(TASK), "target": TASK["target"], "base_branch": "main", "base_sha": self.base,
            "allowed_paths": ["msw-ai-technical-core/"], "verified_at": "2026-11-07T00:00:00Z",
            **{key: True for key in r.task_context.TRUE_FLAGS}, **{key: False for key in r.task_context.FALSE_FLAGS}}
        self.pin = hashlib.sha256(r.task_context.context_bytes(self.context)).hexdigest()
        git(self.root, "switch", "-qc", "msw-coding-task/" + TASK["id"])
        self.write(PATH, "export const result = 2;\n")
        self.commit()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, amend=False):
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "synthetic candidate", *(["--amend"] if amend else []))

    def evidence(self):
        # These are intentionally synthetic controller receipts. Tests exercise
        # their binding and tamper rejection; they do not claim to run a model.
        policy = r._policy()
        changes = r.controller.inspect_changes(self.root, self.base, policy)
        checks = r.controller.select_checks(changes)
        report = {"status": "PASS", "base_sha": self.base, "changes": changes, "production_verified": False,
                  "tests": [{"id": name, "command": check["command"], "cwd": check["cwd"], "status": "PASS",
                             "exit_code": 0, "checks": check["minimum"], "output_sha256": "e" * 64,
                             "duration_seconds": 0.125} for name, check in checks.items()]}
        proof = {"status": "PASS", "base_sha": self.base, "commit": git(self.root, "rev-parse", "HEAD"),
                 "tree": git(self.root, "rev-parse", "HEAD^{tree}")}
        return report, proof

    def bound(self, report=None, basic=None):
        expected_report, expected_basic = self.evidence()
        return r.bind_result(self.context, self.pin, expected_report if report is None else report,
                             expected_basic if basic is None else basic, root=self.root)

    def verified(self, http=None, proof=None, **kwargs):
        proof = self.bound() if proof is None else proof
        http = FakeHTTP(self, proof) if http is None else http
        return r.verify_pr(self.context, self.pin, proof, 17, token=TOKEN, http=http, **kwargs)

    def test_bind_matches_actual_commit_tree_task_and_scope_without_grant(self):
        proof = self.bound()
        self.assertEqual(proof["commit"], git(self.root, "rev-parse", "HEAD"))
        self.assertEqual(proof["tree"], git(self.root, "rev-parse", "HEAD^{tree}"))
        self.assertEqual(proof["binding"]["request_id"], TASK["request_id"])
        self.assertEqual(proof["binding"]["file_sha256"], self.context["file_sha256"])
        self.assertNotEqual(proof["binding"]["file_sha256"], proof["binding"]["request_sha256"])
        self.assertEqual(proof["changes"][0]["git_blob_sha"], git(self.root, "rev-parse", "HEAD:" + PATH))
        self.assertTrue(all(proof[key] is True for key in r.TRUE_FLAGS))
        self.assertTrue(all(proof[key] is False for key in r.FALSE_FLAGS))
        self.assertEqual(r.validate_proof(self.context, self.pin, proof), proof)

    def test_context_pin_is_external_and_includes_final_lf(self):
        other = copy.deepcopy(self.context)
        other["queue_head_sha"] = "3" * 40
        for context, pin in ((self.context, "0" * 64), (other, self.pin),
                             (self.context, hashlib.sha256(r.task_context.context_bytes(self.context)[:-1]).hexdigest())):
            with self.subTest(pin=pin):
                with self.assertRaisesRegex(r.Blocked, "context_pin_mismatch"):
                    r.expected_pr_binding(context, pin)

    def test_context_file_requires_canonical_bytes_and_rejects_duplicates(self):
        path = self.artifacts / "context.json"
        path.write_bytes(r.task_context.context_bytes(self.context))
        self.assertEqual(r.load_context(path, self.pin), self.context)
        for raw in (json.dumps(self.context, indent=2).encode(), b'{"schema":"a","schema":"b"}',
                    r.task_context.context_bytes(self.context) + b"\n", b'{"x":NaN}'):
            path.write_bytes(raw)
            with self.assertRaises(r.Blocked):
                r.load_context(path, self.pin)

    def test_expected_block_is_deterministic_before_candidate_exists(self):
        block = r.expected_pr_binding(self.context, self.pin)
        self.assertEqual(block, self.bound()["pr_binding_block"])
        self.assertEqual(block.splitlines()[0], r.START)
        self.assertEqual(block.splitlines()[-1], r.END)
        body = "Description\n\n" + block + "\nOther details."
        self.assertEqual(r.require_pr_binding(body, block), hashlib.sha256(body.encode()).hexdigest())

    def test_binding_rejects_duplicates_malformed_markers_inline_and_changed_hash(self):
        block = r.expected_pr_binding(self.context, self.pin)
        cases = ["", block + "\n" + block, "prefix " + block, block + " suffix",
                 block.replace(self.pin, "0" * 64), block.replace(r.START, r.END),
                 block + "\n<!-- msw-coding-task-binding:other -->", block.replace("\n", "\r\n")]
        for body in cases:
            with self.subTest(body_prefix=body[:30]):
                with self.assertRaises(r.Blocked):
                    r.require_pr_binding(body, block)

    def test_body_size_nul_and_invalid_unicode_fail_with_fixed_reason(self):
        block = r.expected_pr_binding(self.context, self.pin)
        for body in (None, block + "\x00", block + "x" * 65536, block + "\ud800"):
            with self.assertRaisesRegex(r.Blocked, "invalid_pr_body"):
                r.require_pr_binding(body, block)

    def test_basic_proof_requires_exact_head_tree_base_and_status(self):
        report, basic = self.evidence()
        for updates in ({"commit": "a" * 40}, {"tree": "b" * 40}, {"base_sha": "c" * 40},
                        {"status": "NO_CHANGES"}, {"extra": "ignored?"}):
            with self.subTest(updates=updates):
                with self.assertRaises(r.Blocked):
                    self.bound(report, {**basic, **updates})

    def test_branch_must_be_exact_task_branch_without_suffix(self):
        report, basic = self.evidence()
        git(self.root, "branch", "-m", "msw-coding-task/" + TASK["id"] + "-extra")
        with self.assertRaisesRegex(r.Blocked, "branch_mismatch"):
            r.bind_result(self.context, self.pin, report, basic, root=self.root)

    def test_candidate_requires_exactly_one_parent_at_pinned_base(self):
        self.write(PATH, "export const result = 3;\n")
        self.commit()
        with self.assertRaisesRegex(r.Blocked, "commit_mismatch"):
            self.bound()

    def test_dirty_worktree_staging_and_untracked_source_are_rejected(self):
        report, basic = self.evidence()
        self.write(PATH, "export const result = 999;\n")
        with self.assertRaisesRegex(r.Blocked, "candidate_not_clean"):
            r.bind_result(self.context, self.pin, report, basic, root=self.root)
        git(self.root, "add", PATH)
        with self.assertRaisesRegex(r.Blocked, "candidate_not_clean"):
            r.bind_result(self.context, self.pin, report, basic, root=self.root)
        git(self.root, "restore", "--staged", "--worktree", PATH)
        self.write("msw-ai-technical-core/untracked.mjs", "export const hidden = true;\n")
        with self.assertRaisesRegex(r.Blocked, "candidate_not_clean"):
            r.bind_result(self.context, self.pin, report, basic, root=self.root)

    def test_untracked_controller_report_is_not_candidate_source(self):
        self.write(".msw-github-engine/validate.json", "{}\n")
        self.assertEqual(self.bound()["status"], "PASS")

    def test_assume_unchanged_cannot_hide_replaced_candidate_content(self):
        report, basic = self.evidence()
        git(self.root, "update-index", "--assume-unchanged", PATH)
        self.write(PATH, "export const result = 999;\n")
        with self.assertRaisesRegex(r.Blocked, "candidate_content_mismatch"):
            r.bind_result(self.context, self.pin, report, basic, root=self.root)

    def test_report_must_equal_immutable_change_set_and_production_false(self):
        report, basic = self.evidence()
        updates = [{"status": "FAIL"}, {"base_sha": "a" * 40}, {"production_verified": True},
                   {"production_verified": 0}, {"changes": []}, {"extra": True},
                   {"changes": [{**report["changes"][0], "sha256": "0" * 64}]}]
        for changes in updates:
            with self.subTest(keys=list(changes)):
                with self.assertRaisesRegex(r.Blocked, "report_mismatch"):
                    self.bound({**report, **changes}, basic)

    def test_task_target_cannot_expand_to_another_globally_allowed_engine(self):
        self.write("msw-research-engine/extra.py", "result = 7\n")
        self.commit(amend=True)
        with self.assertRaisesRegex(r.Blocked, "task_scope_mismatch"):
            self.bound()

    def test_existing_tests_are_immutable_at_git_level(self):
        report, basic = self.evidence()
        self.write(TEST_PATH, "// disabled tests\n")
        self.commit(amend=True)
        basic.update(commit=git(self.root, "rev-parse", "HEAD"), tree=git(self.root, "rev-parse", "HEAD^{tree}"))
        with self.assertRaisesRegex(r.Blocked, "existing_test_changed_or_deleted"):
            r.bind_result(self.context, self.pin, report, basic, root=self.root)

    def test_new_test_requires_its_independent_selected_check(self):
        self.write("msw-ai-technical-core/tests/extra.test.mjs", "// synthetic added test\n")
        self.commit(amend=True)
        report, basic = self.evidence()
        self.assertEqual(len(self.bound(report, basic)["tests"]), 2)
        report["tests"] = report["tests"][:1]
        with self.assertRaisesRegex(r.Blocked, "missing_test_evidence"):
            self.bound(report, basic)

    def test_report_checks_require_exact_commands_and_real_integer_counts(self):
        report, basic = self.evidence()
        cases = [{"command": ["echo", "PASS"]}, {"cwd": "/tmp"}, {"status": "FAIL"},
                 {"exit_code": False}, {"exit_code": 1}, {"checks": True}, {"checks": 0},
                 {"output_sha256": "main"}, {"duration_seconds": float("nan")}, {"extra": 1}]
        for update in cases:
            with self.subTest(update=update):
                bad = copy.deepcopy(report)
                bad["tests"][0].update(update)
                with self.assertRaises(r.Blocked):
                    self.bound(bad, basic)

    def test_proof_cannot_claim_execution_ack_acceptance_merge_or_deploy(self):
        proof = self.bound()
        for flag in r.FALSE_FLAGS:
            bad = {**proof, flag: True}
            with self.subTest(flag=flag):
                with self.assertRaisesRegex(r.Blocked, "invalid_task_proof"):
                    r.validate_proof(self.context, self.pin, bad)

    def test_verify_reads_real_pr_commit_files_blobs_and_rechecks_pr(self):
        proof = self.bound()
        http = FakeHTTP(self, proof)
        result = self.verified(http=http, proof=proof)
        self.assertEqual(result["status"], "PROPOSED_VERIFIED")
        self.assertTrue(result["pr_verified"])
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["central_ack_verified"])
        self.assertFalse(result["merged_verified"])
        self.assertFalse(result["deployed_verified"])
        self.assertEqual(http.pr_calls, 2)
        self.assertEqual([call[0] for call in http.calls], ["GET"] * 5)

    def test_pr_number_and_token_cannot_select_a_url_or_header(self):
        proof = self.bound()
        for number in (0, -1, True, "17/../../", 2147483648):
            http = FakeHTTP(self, proof)
            with self.subTest(number=number):
                with self.assertRaisesRegex(r.Blocked, "invalid_pr_number"):
                    r.verify_pr(self.context, self.pin, proof, number, token=TOKEN, http=http)
                self.assertEqual(http.calls, [])
        for token in (None, "", "x\r\nHeader: value", "x y"):
            http = FakeHTTP(self, proof)
            with self.assertRaisesRegex(r.Blocked, "read_token"):
                r.verify_pr(self.context, self.pin, proof, 17, token=token, http=http)
            self.assertEqual(http.calls, [])

    def test_pr_must_be_open_unmerged_and_in_exact_repository_on_both_sides(self):
        proof = self.bound()
        for side in ("base", "head"):
            for key, value in (("id", 1), ("id", True), ("full_name", "attacker/KonyWarsaw")):
                http = FakeHTTP(self, proof)
                http.values["/pulls/17"][side]["repo"][key] = value
                with self.assertRaisesRegex(r.Blocked, "repository_mismatch"):
                    self.verified(http, proof)
        for update in ({"state": "closed"}, {"merged": True}, {"merged": 0}, {"number": 18}, {"changed_files": True}):
            http = FakeHTTP(self, proof)
            http.values["/pulls/17"].update(update)
            with self.assertRaises(r.Blocked):
                self.verified(http, proof)

    def test_base_drift_requires_rebase_and_retest(self):
        proof = self.bound()
        http = FakeHTTP(self, proof)
        http.values["/pulls/17"]["base"]["sha"] = "f" * 40
        with self.assertRaisesRegex(r.Blocked, "stale_pr_base_rebase_retest_required"):
            self.verified(http, proof)

    def test_pr_branch_commit_and_body_must_match_the_pinned_task(self):
        proof = self.bound()
        for field, value in (("ref", proof["branch"] + "-extra"), ("sha", "f" * 40)):
            http = FakeHTTP(self, proof)
            http.values["/pulls/17"]["head"][field] = value
            with self.assertRaisesRegex(r.Blocked, "pr_head_mismatch"):
                self.verified(http, proof)
        http = FakeHTTP(self, proof)
        http.values["/pulls/17"]["body"] = "Claims success without task identity."
        with self.assertRaisesRegex(r.Blocked, "ambiguous_pr_binding"):
            self.verified(http, proof)

    def test_independent_commit_tree_and_single_parent_are_required(self):
        proof = self.bound()
        for update in ({"sha": "a" * 40}, {"tree": {"sha": "b" * 40}}, {"parents": []},
                       {"parents": [{"sha": self.base}, {"sha": "c" * 40}]},
                       {"parents": [{"sha": "c" * 40}]}):
            http = FakeHTTP(self, proof)
            http.values["/git/commits/" + proof["commit"]].update(update)
            with self.assertRaisesRegex(r.Blocked, "pr_commit_mismatch"):
                self.verified(http, proof)

    def test_every_remote_path_status_and_git_blob_sha_must_match(self):
        proof = self.bound()
        for update in ({"filename": "msw-ai-technical-core/other.mjs"}, {"sha": "a" * 40},
                       {"status": "added"}, {"status": "copied"}, {"previous_filename": PATH}):
            http = FakeHTTP(self, proof)
            http.values["/pulls/17/files?per_page=100&page=1"][0].update(update)
            with self.assertRaises(r.Blocked):
                self.verified(http, proof)
        for count in (0, 2):
            http = FakeHTTP(self, proof)
            files = http.values["/pulls/17/files?per_page=100&page=1"]
            http.values["/pulls/17/files?per_page=100&page=1"] = files * count
            with self.assertRaisesRegex(r.Blocked, "pr_file_count_mismatch"):
                self.verified(http, proof)

    def test_rename_is_explicit_delete_add_with_both_blob_identities(self):
        new = "msw-ai-technical-core/renamed.mjs"
        git(self.root, "mv", PATH, new)
        self.commit(amend=True)
        proof = self.bound()
        http = FakeHTTP(self, proof)
        added = next(item for item in proof["changes"] if item["change"] == "added")
        http.values["/pulls/17"]["changed_files"] = 1
        http.values["/pulls/17/files?per_page=100&page=1"] = [
            {"filename": new, "previous_filename": PATH, "status": "renamed", "sha": added["git_blob_sha"]}]
        self.assertTrue(self.verified(http, proof)["pr_verified"])
        http = FakeHTTP(self, proof)
        http.values["/pulls/17"]["changed_files"] = 1
        http.values["/pulls/17/files?per_page=100&page=1"] = [
            {"filename": new, "previous_filename": "msw-ai-technical-core/unrelated.mjs", "status": "renamed",
             "sha": added["git_blob_sha"]}]
        with self.assertRaisesRegex(r.Blocked, "pr_rename_mismatch"):
            self.verified(http, proof)

    def test_remote_blob_bytes_are_hashed_independently(self):
        proof = self.bound()
        path = "/git/blobs/" + proof["changes"][0]["git_blob_sha"]
        for update in ({"sha": "f" * 40}, {"size": True}, {"size": 204801}, {"encoding": "utf-8"},
                       {"content": "%%%"}, {"content": base64.b64encode(b"tamper\n").decode()},
                       {"content": "x" * 409601}):
            http = FakeHTTP(self, proof)
            http.values[path].update(update)
            with self.assertRaises(r.Blocked):
                self.verified(http, proof)

    def test_pr_is_rechecked_after_file_reads_for_concurrent_edits(self):
        proof = self.bound()
        http = FakeHTTP(self, proof)
        http.on_recheck = lambda pr: pr.update(body=pr["body"] + "Concurrent description edit.\n")
        with self.assertRaisesRegex(r.Blocked, "pr_changed_during_verification"):
            self.verified(http, proof)
        http = FakeHTTP(self, proof)
        http.on_recheck = lambda pr: pr["head"].update(sha="f" * 40)
        with self.assertRaisesRegex(r.Blocked, "pr_head_mismatch"):
            self.verified(http, proof)

    def test_http_errors_reveal_only_fixed_code_and_numeric_status(self):
        proof = self.bound()
        http = FakeHTTP(self, proof)
        http.failure = r.bridge.BridgeError(TOKEN, stage="pr", status=503)
        with self.assertRaises(r.Blocked) as caught:
            self.verified(http, proof)
        self.assertEqual(str(caught.exception), "github_read_failed")
        self.assertEqual(caught.exception.http_status, 503)
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_cli_requires_explicit_context_pin_and_creates_exclusive_output(self):
        report, basic = self.evidence()
        for name, raw in (("context.json", r.task_context.context_bytes(self.context)),
                          ("report.json", r._canonical(report)), ("proof.json", r._canonical(basic))):
            (self.artifacts / name).write_bytes(raw)
        output = self.artifacts / "bound.json"
        args = ["bind", "--context", str(self.artifacts / "context.json"), "--expected-context-sha256", self.pin,
                "--report", str(self.artifacts / "report.json"), "--proof", str(self.artifacts / "proof.json"),
                "--output", str(output), "--root", str(self.root)]
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertEqual(r.main(args, environ={}), 0)
        self.assertEqual(json.loads(stream.getvalue())["status"], "PASS")
        saved = output.read_bytes()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(r.main(args, environ={}), 1)
        self.assertEqual(output.read_bytes(), saved)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            r.main(args[:3] + args[5:], environ={})
        self.assertEqual(caught.exception.code, 2)

    def test_context_artifact_symlink_and_hardlink_are_rejected(self):
        original = self.artifacts / "original.json"
        original.write_bytes(r.task_context.context_bytes(self.context))
        alias = self.artifacts / "alias.json"
        alias.symlink_to(original)
        with self.assertRaisesRegex(r.Blocked, "artifact_read_failed"):
            r.load_context(alias, self.pin)
        alias.unlink()
        os.link(original, alias)
        with self.assertRaisesRegex(r.Blocked, "invalid_artifact_file"):
            r.load_context(alias, self.pin)


if __name__ == "__main__":
    unittest.main()
