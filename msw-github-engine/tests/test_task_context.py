"""Pinned queue data, exact public context and fail-closed scope regressions."""
import base64
from contextlib import redirect_stdout
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1]
ROOT = SOURCE.parent
spec = importlib.util.spec_from_file_location("msw_task_context", SOURCE / "task_context.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

QUEUE_COMMIT = "1" * 40
QUEUE_HEAD = "2" * 40
BASE_SHA = "a" * 40
ROOT_TREE = "b" * 40
TASK_TREE = "c" * 40
TOKEN = "synthetic-read-token-never-log-this"
FIXTURE = (ROOT / "msw-coding-bridge/tests/fixtures/task-utf8.json").read_bytes()
TASK = c.bridge.decode_json(FIXTURE)


def task_bytes(**changes):
    value = {**TASK, **changes}
    unsigned = {key: item for key, item in value.items() if key != "request_sha256"}
    value["request_sha256"] = hashlib.sha256(c.bridge.canonical(unsigned)).hexdigest()
    return c.bridge.canonical(value) + b"\n"


class FakeHTTP:
    """Only synthetic, bounded GitHub responses; never open a network socket."""

    def __init__(self, raw=FIXTURE, *, task_id=TASK["id"], head=QUEUE_HEAD):
        self.calls = []
        self.failure = None
        self.blob_sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        self.values = {
            "/git/commits/" + QUEUE_COMMIT: {"sha": QUEUE_COMMIT, "tree": {"sha": ROOT_TREE}},
            "/git/trees/" + ROOT_TREE: {"sha": ROOT_TREE, "truncated": False, "tree": [
                {"path": "msw-coding-tasks", "type": "tree", "mode": "040000", "sha": TASK_TREE}]},
            "/git/trees/" + TASK_TREE: {"sha": TASK_TREE, "truncated": False, "tree": [
                {"path": task_id + ".json", "type": "blob", "mode": "100644", "sha": self.blob_sha, "size": len(raw)}]},
            "/git/blobs/" + self.blob_sha: {"sha": self.blob_sha, "size": len(raw), "encoding": "base64",
                                           "content": base64.encodebytes(raw).decode("ascii")},
            "/git/ref/heads/" + c.bridge.QUEUE_BRANCH: {"ref": "refs/heads/" + c.bridge.QUEUE_BRANCH,
                                                       "object": {"type": "commit", "sha": head}},
            "/compare/" + QUEUE_COMMIT + "..." + head + "?per_page=1": {
                "status": "ahead", "ahead_by": 1, "behind_by": 0,
                "base_commit": {"sha": QUEUE_COMMIT}, "merge_base_commit": {"sha": QUEUE_COMMIT}},
        }

    def request(self, method, url, token, payload=None, *, stage, max_bytes):
        self.calls.append((method, url, token, payload, stage, max_bytes))
        if method != "GET" or payload is not None or not url.startswith(c.bridge.API_ROOT + "/"):
            raise AssertionError("Only fixed-repository GET is allowed")
        if self.failure:
            raise self.failure
        return 200, copy.deepcopy(self.values[url[len(c.bridge.API_ROOT):]])


class ContextTests(unittest.TestCase):
    def prepare(self, raw=FIXTURE, *, http=None, **kwargs):
        http = FakeHTTP(raw) if http is None else http
        values = {"task_id": TASK["id"], "queue_commit": QUEUE_COMMIT,
                  "file_sha256": hashlib.sha256(raw).hexdigest(), "base_sha": BASE_SHA,
                  "token": TOKEN, "http": http, **kwargs}
        return c.prepare_context(**values)

    def test_utf8_fixture_is_bound_to_commit_hash_scope_and_no_grant(self):
        http = FakeHTTP()
        context = self.prepare(http=http)
        self.assertEqual(set(context), c.CONTEXT_KEYS)
        self.assertEqual(context["task"], TASK)
        self.assertEqual(context["request_sha256"], "c5260202d2428fc1db05ae50bc5e5461fb835fb64ba9d9ac69f1ebd72800b17a")
        self.assertNotEqual(context["file_sha256"], context["request_sha256"])
        self.assertEqual(context["base_branch"], "main")
        self.assertEqual(context["base_sha"], BASE_SHA)
        self.assertEqual(context["queue_commit"], QUEUE_COMMIT)
        self.assertEqual(context["queue_head_sha"], QUEUE_HEAD)
        self.assertEqual(context["allowed_paths"], ["msw-ai-technical-core/"])
        for flag in c.FALSE_FLAGS:
            self.assertIs(context[flag], False)
        for flag in c.TRUE_FLAGS:
            self.assertIs(context[flag], True)
        self.assertEqual(len(http.calls), 6)
        self.assertTrue(all(call[0] == "GET" and call[3] is None and call[5] == 131072 for call in http.calls))
        self.assertEqual(http.calls[-1][1], c.bridge.API_ROOT + "/compare/" + QUEUE_COMMIT + "..." + QUEUE_HEAD + "?per_page=1")
        self.assertNotIn(TOKEN, c.context_bytes(context).decode())

    def test_head_equal_to_commit_uses_fresh_ref_without_compare(self):
        http = FakeHTTP(head=QUEUE_COMMIT)
        context = self.prepare(http=http)
        self.assertEqual(context["queue_head_sha"], QUEUE_COMMIT)
        self.assertEqual(len(http.calls), 5)
        self.assertIn("/git/ref/heads/", http.calls[-1][1])

    def test_invalid_identity_and_full_sha_inputs_block_before_network(self):
        for key, values in {
            "task_id": [None, True, "short", "a" * 31 + "A", "../" + "a" * 32],
            "queue_commit": ["main", "1" * 7, "A" * 40, "https://elsewhere.invalid"],
            "file_sha256": ["a" * 63, "A" * 64, "a" * 64 + "\n"],
            "base_sha": ["main", "a" * 39, "a" * 40 + "/../main"],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    http = FakeHTTP()
                    with self.assertRaisesRegex(c.Blocked, "invalid_" + key):
                        self.prepare(http=http, **{key: value})
                    self.assertEqual(http.calls, [])

    def test_tokens_are_memory_only_and_control_characters_are_rejected(self):
        for token in (None, "", "fixture\r\nInjected: x", "fixture token", "é", "x" * 16385):
            http = FakeHTTP()
            with self.subTest(token_type=type(token).__name__):
                with self.assertRaisesRegex(c.Blocked, "read_token"):
                    self.prepare(http=http, token=token)
                self.assertEqual(http.calls, [])

    def test_request_hash_cannot_be_substituted_for_whole_file_hash(self):
        with self.assertRaisesRegex(c.Blocked, "task_file_digest_mismatch"):
            self.prepare(file_sha256=TASK["request_sha256"])

    def test_internal_digest_and_path_identity_are_independent_checks(self):
        changed = {**TASK, "title": "Changed without recomputing digest"}
        with self.assertRaisesRegex(c.Blocked, "task_digest_mismatch"):
            self.prepare(c.bridge.canonical(changed) + b"\n")
        other = "f" * 32
        with self.assertRaisesRegex(c.Blocked, "task_path_identity_mismatch"):
            self.prepare(http=FakeHTTP(task_id=other), task_id=other)

    def test_exact_canonical_file_encoding_and_single_lf_required(self):
        alternatives = [FIXTURE[:-1], FIXTURE + b"\n", json.dumps(TASK, ensure_ascii=False, indent=2).encode() + b"\n",
                        json.dumps(TASK, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode() + b"\n"]
        for raw in alternatives:
            with self.subTest(size=len(raw)):
                with self.assertRaisesRegex(c.Blocked, "noncanonical_task_file"):
                    self.prepare(raw)

    def test_duplicate_keys_nonfinite_and_invalid_utf8_rejected(self):
        for raw in (b'{"schema":"a","schema":"b"}\n', b'{"value":NaN}\n', b'\xff\n'):
            with self.subTest(raw=repr(raw)):
                with self.assertRaises(c.Blocked):
                    self.prepare(raw)

    def test_existing_validator_rejects_private_secret_and_extra_fields(self):
        for changes, error in (({"public_data": False}, "task_not_public"),
                               ({"objective": "ghp_" + "A" * 40}, "secret_like_task_content"),
                               ({"execution_authorized": True}, "invalid_task_schema"),
                               ({"acceptance": []}, "invalid_acceptance")):
            with self.subTest(error=error):
                with self.assertRaisesRegex(c.Blocked, error):
                    self.prepare(task_bytes(**changes))

    def test_each_main_target_gets_only_its_registered_scope(self):
        for target, paths in c.MAIN_SCOPES.items():
            with self.subTest(target=target):
                context = self.prepare(task_bytes(target=target))
                self.assertEqual(context["allowed_paths"], paths)
                self.assertEqual(context["base_branch"], "main")
                self.assertFalse(context["execution_authorized"])

    def test_runtime_owned_llm_and_wordpress_are_explicitly_blocked(self):
        for target in ("runtime", "owned-llm", "wordpress"):
            with self.subTest(target=target):
                with self.assertRaisesRegex(c.Blocked, "unsupported_executor_target"):
                    self.prepare(task_bytes(target=target))

    def test_immutable_tree_prohibits_symlink_gitlink_executable_and_duplicates(self):
        for changes in ({"mode": "120000"}, {"mode": "160000", "type": "commit"},
                        {"mode": "100755"}, {"sha": "main"}, {"size": True}, {"size": 16385}):
            http = FakeHTTP()
            http.values["/git/trees/" + TASK_TREE]["tree"][0].update(changes)
            with self.subTest(changes=changes):
                with self.assertRaises(c.Blocked):
                    self.prepare(http=http)
        http = FakeHTTP()
        entries = http.values["/git/trees/" + TASK_TREE]["tree"]
        entries.append(copy.deepcopy(entries[0]))
        with self.assertRaisesRegex(c.Blocked, "ambiguous"):
            self.prepare(http=http)

    def test_truncated_trees_wrong_commit_and_wrong_tree_sha_fail_closed(self):
        cases = [("/git/commits/" + QUEUE_COMMIT, "sha", "d" * 40),
                 ("/git/trees/" + ROOT_TREE, "truncated", True),
                 ("/git/trees/" + TASK_TREE, "sha", "d" * 40)]
        for path, key, value in cases:
            http = FakeHTTP()
            http.values[path][key] = value
            with self.subTest(path=path, key=key):
                with self.assertRaises(c.Blocked):
                    self.prepare(http=http)

    def test_blob_identity_size_and_base64_are_verified(self):
        for changes in ({"size": True}, {"size": 1}, {"sha": "d" * 40}, {"encoding": "utf-8"},
                        {"content": "%%%"}, {"content": base64.b64encode(b"x" * len(FIXTURE)).decode()},
                        {"content": "x" * 32769}):
            http = FakeHTTP()
            http.values["/git/blobs/" + http.blob_sha].update(changes)
            with self.subTest(keys=list(changes)):
                with self.assertRaises(c.Blocked):
                    self.prepare(http=http)

    def test_fresh_ref_must_name_exact_queue_and_a_full_commit(self):
        for changes in ({"ref": "refs/heads/main"}, {"object": {"type": "tag", "sha": QUEUE_HEAD}},
                        {"object": {"type": "commit", "sha": "main"}}):
            http = FakeHTTP()
            http.values["/git/ref/heads/" + c.bridge.QUEUE_BRANCH].update(changes)
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(c.Blocked, "invalid_queue_ref"):
                    self.prepare(http=http)

    def test_unrelated_or_ambiguous_queue_membership_is_rejected(self):
        changes_list = [{"status": "diverged"}, {"status": "behind"}, {"status": "identical"},
                        {"ahead_by": 0}, {"ahead_by": True}, {"behind_by": 1},
                        {"base_commit": {"sha": QUEUE_HEAD}}, {"merge_base_commit": {"sha": QUEUE_HEAD}}]
        for changes in changes_list:
            http = FakeHTTP()
            http.values["/compare/" + QUEUE_COMMIT + "..." + QUEUE_HEAD + "?per_page=1"].update(changes)
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(c.Blocked, "commit_not_in_queue"):
                    self.prepare(http=http)

    def test_context_validates_binding_flags_and_exact_keys_without_network(self):
        original = self.prepare()
        variations = [{"extra": "field"}, {"schema": "different"}, {"status": "EXECUTED"},
                      {"repository": "other/repository"}, {"queue_branch": "main"}, {"base_branch": "other"},
                      {"base_sha": "short"}, {"task_id": "f" * 32}, {"file_sha256": "f" * 64},
                      {"target": "portal"}, {"allowed_paths": [""]}, {"verified_at": "2026-11-07"},
                      {"task_path": "msw-coding-tasks/../secret.json"}]
        variations.extend({flag: True} for flag in c.FALSE_FLAGS)
        variations.extend({flag: 1} for flag in c.TRUE_FLAGS)
        with patch.object(c.bridge, "JsonHTTP", side_effect=AssertionError("No network during validation")):
            self.assertEqual(c.validate_context(original), original)
            for changes in variations:
                with self.subTest(changes=changes):
                    value = {**copy.deepcopy(original), **changes}
                    with self.assertRaises(c.Blocked):
                        c.validate_context(value)

    def test_canonical_context_preserves_unicode_and_hashes_exact_file_bytes(self):
        context = self.prepare()
        encoded = c.context_bytes(context)
        self.assertEqual(encoded, c.bridge.canonical(context) + b"\n")
        self.assertIn("Łódź".encode(), encoded)
        self.assertIn("\u2028".encode(), encoded)
        self.assertIn("\u2029".encode(), encoded)
        self.assertEqual(encoded.count(b"\n"), 1)
        self.assertNotEqual(hashlib.sha256(encoded).hexdigest(), hashlib.sha256(encoded[:-1]).hexdigest())

    def test_target_catalog_or_policy_cannot_expand_scope(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "msw-coding-bridge").mkdir()
            (root / "msw-github-engine").mkdir()
            shutil.copyfile(SOURCE / "policy.json", root / "msw-github-engine/policy.json")
            original = json.loads((ROOT / "msw-coding-bridge/targets.json").read_text())
            path = root / "msw-coding-bridge/targets.json"
            for changes in ({"branch": "msw/engine-runtime-20261104"}, {"paths": [""]},
                            {"paths": ["msw-ai-technical-core/", "msw-research-engine/"]},
                            {"execution_support": "chatgpt_branch_worker"}):
                data = copy.deepcopy(original)
                data["targets"]["technical-core"].update(changes)
                path.write_text(json.dumps(data))
                with self.subTest(changes=changes):
                    with self.assertRaisesRegex(c.Blocked, "target_scope_mismatch"):
                        self.prepare(repository_root=root)
            path.write_text(json.dumps(original))
            (root / "msw-github-engine/policy.json").write_text("{}")
            with self.assertRaisesRegex(c.Blocked, "invalid_target_policy"):
                self.prepare(repository_root=root)

    def cli(self, http, *extra, environ=None):
        out = io.StringIO()
        args = ["--task-id", TASK["id"], "--queue-commit", QUEUE_COMMIT,
                "--file-sha256", hashlib.sha256(FIXTURE).hexdigest(), "--base-sha", BASE_SHA, *extra]
        with patch.object(c.bridge, "JsonHTTP", return_value=http), redirect_stdout(out):
            status = c.main(args, {"GITHUB_TOKEN": TOKEN} if environ is None else environ)
        return status, out.getvalue()

    def test_cli_output_file_and_short_receipt_bind_exact_bytes(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "context.json"
            code, stdout = self.cli(FakeHTTP(), "--output", str(path))
            self.assertEqual(code, 0)
            raw = path.read_bytes()
            receipt = json.loads(stdout)
            self.assertEqual(receipt["context_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(receipt["status"], "CONTEXT_READY")
            self.assertFalse(receipt["execution_authorized"])
            self.assertEqual(c.context_bytes(c.bridge.decode_json(raw)), raw)
            self.assertNotIn("objective", stdout)
            self.assertNotIn(TOKEN, stdout)

    def test_cli_stdout_without_output_is_exact_canonical_context(self):
        code, stdout = self.cli(FakeHTTP())
        self.assertEqual(code, 0)
        context = json.loads(stdout)
        self.assertEqual(stdout.encode(), c.context_bytes(context))
        self.assertNotIn(TOKEN, stdout)

    def test_cli_preserves_existing_file_and_never_follows_output_symlink(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "existing.json"
            path.write_bytes(b"preserve-existing-data")
            link = Path(name) / "link.json"
            link.symlink_to(path)
            for destination in (path, link):
                with self.subTest(destination=destination.name):
                    code, stdout = self.cli(FakeHTTP(), "--output", str(destination))
                    self.assertEqual(code, 1)
                    self.assertEqual(json.loads(stdout)["status"], "BLOCKED")
                    self.assertEqual(path.read_bytes(), b"preserve-existing-data")

    def test_cli_failures_never_print_token_url_or_remote_exception_text(self):
        for failure in (c.bridge.BridgeError("http_error", stage="queue_commit", status=302),
                        RuntimeError("remote body contains " + TOKEN + " https://private.invalid")):
            http = FakeHTTP()
            http.failure = failure
            code, stdout = self.cli(http)
            self.assertEqual(code, 1)
            result = json.loads(stdout)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertFalse(result["execution_authorized"])
            self.assertNotIn(TOKEN, stdout)
            self.assertNotIn("private.invalid", stdout)
        http = FakeHTTP()
        code, stdout = self.cli(http, environ={})
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout)["error"], "missing_or_invalid_read_token")
        self.assertEqual(http.calls, [])


if __name__ == "__main__":
    unittest.main()
