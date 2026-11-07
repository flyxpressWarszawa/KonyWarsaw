import base64
import hashlib
import json
import os
from pathlib import Path
import types
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from msw_engine import github_verify as verify
from msw_engine.tasks import TaskError


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.raw = b"print('hello MSW')\n"
        self.commit = "a" * 40
        self.root = "b" * 40
        self.subtree = "c" * 40
        self.blob = hashlib.sha1(b"blob " + str(len(self.raw)).encode() + b"\0" + self.raw).hexdigest()
        self.job = {"schema": verify.SCHEMA, "request_id": "msw-test-123456", "commit_sha": self.commit,
                    "path": "msw-autonomous-engine/test.py", "expected_sha256": hashlib.sha256(self.raw).hexdigest()}
        self.responses = {
            "/git/commits/" + self.commit: {"sha": self.commit, "tree": {"sha": self.root}},
            "/git/trees/" + self.root: {"sha": self.root, "truncated": False, "tree": [
                {"path": "msw-autonomous-engine", "mode": "040000", "type": "tree", "sha": self.subtree}]},
            "/git/trees/" + self.subtree: {"sha": self.subtree, "truncated": False, "tree": [
                {"path": "test.py", "mode": "100644", "type": "blob", "sha": self.blob}]},
            "/git/blobs/" + self.blob: {"sha": self.blob, "encoding": "base64", "size": len(self.raw),
                                       "content": base64.b64encode(self.raw).decode()},
        }

    def run_job(self, job=None):
        with patch.object(verify, "_get", side_effect=lambda url: self.responses[url]):
            return verify.handle(self.job if job is None else job, Path("."))

    def test_exact_commit_tree_blob_and_digest(self):
        result = self.run_job()
        self.assertEqual(result["commit_sha"], self.commit)
        self.assertEqual(result["root_tree_sha"], self.root)
        self.assertEqual(result["git_blob_sha"], self.blob)
        self.assertEqual(result["content_sha256"], self.job["expected_sha256"])
        self.assertTrue(result["github_file_verified"])
        self.assertFalse(result["tests_verified"])
        self.assertFalse(result["deployed_verified"])
        self.assertNotIn(self.raw.decode(), json.dumps(result))

    def test_rejects_mismatch_even_if_valid_git_blob(self):
        self.job["expected_sha256"] = "d" * 64
        with self.assertRaisesRegex(TaskError, "github_expected_sha256_mismatch"):
            self.run_job()

    def test_rejects_forged_blob(self):
        self.responses["/git/blobs/" + self.blob]["content"] = base64.b64encode(b"x" * len(self.raw)).decode()
        with self.assertRaisesRegex(TaskError, "github_blob_hash_mismatch"):
            self.run_job()

    def test_commit_identity_must_match(self):
        self.responses["/git/commits/" + self.commit]["sha"] = "d" * 40
        with self.assertRaisesRegex(TaskError, "github_commit_not_verified"):
            self.run_job()

    def test_truncated_tree_and_unmatched_tree_rejected(self):
        for key, value in [("truncated", True), ("sha", "d" * 40)]:
            with self.subTest(key=key):
                tree = self.responses["/git/trees/" + self.root]
                original = tree[key]
                tree[key] = value
                with self.assertRaisesRegex(TaskError, "github_tree_not_verified"):
                    self.run_job()
                tree[key] = original

    def test_duplicate_tree_entry_rejected(self):
        entries = self.responses["/git/trees/" + self.subtree]["tree"]
        entries.append(entries[0])
        with self.assertRaisesRegex(TaskError, "github_path_not_found"):
            self.run_job()

    def test_symlink_and_gitlink_are_not_followed(self):
        entry = self.responses["/git/trees/" + self.subtree]["tree"][0]
        for mode, kind in [("120000", "blob"), ("160000", "commit")]:
            with self.subTest(mode=mode):
                entry.update(mode=mode, type=kind)
                with self.assertRaisesRegex(TaskError, "github_tree_entry_forbidden"):
                    self.run_job()

    def test_invalid_base64_and_wrong_size_rejected(self):
        blob = self.responses["/git/blobs/" + self.blob]
        blob["content"] = "!!!!"
        with self.assertRaisesRegex(TaskError, "github_blob_not_verified"):
            self.run_job()
        blob["content"] = base64.b64encode(self.raw).decode()
        blob["size"] = len(self.raw) + 1
        with self.assertRaisesRegex(TaskError, "github_blob_hash_mismatch"):
            self.run_job()

    def test_rejects_oversized_blob(self):
        self.responses["/git/blobs/" + self.blob]["size"] = verify.MAX_FILE + 1
        with self.assertRaisesRegex(TaskError, "github_blob_not_verified"):
            self.run_job()

    def test_invalid_paths_never_touch_network(self):
        paths = ["../test.py", "msw-autonomous-engine/../secret.py", "msw-autonomous-engine/.env.py",
                 "msw-autonomous-engine/secrets/test.py", "msw-autonomous-engine/a%2fb.py",
                 "msw-autonomous-engine/test.yml", "https://evil.example/file.py"]
        for path in paths:
            with self.subTest(path=path), patch.object(verify, "_get") as get:
                job = {**self.job, "path": path}
                with self.assertRaises(TaskError):
                    verify.handle(job, Path("."))
                get.assert_not_called()

    def test_unexpected_field_and_abbreviated_sha_rejected(self):
        for job in [{**self.job, "url": "https://evil.example"}, {**self.job, "commit_sha": "abc123"}]:
            with self.subTest(job=job), patch.object(verify, "_get") as get:
                with self.assertRaises(TaskError):
                    verify.handle(job, Path("."))
                get.assert_not_called()

    def test_http_client_is_fixed_get_and_redirects_disabled(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, maximum): return b'{"ok": true}'
        requests = []
        class Opener:
            def open(self, request, timeout):
                requests.append((request, timeout))
                return Response()
        with patch.object(verify, "build_opener", return_value=Opener()) as builder, \
             patch.dict(os.environ, {"MSW_GITHUB_READ_TOKEN": "test-token"}):
            self.assertEqual(verify._get("/git/commits/" + self.commit), {"ok": True})
        self.assertIs(builder.call_args.args[0], verify.NoRedirect)
        request, timeout = requests[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, verify.API + "/git/commits/" + self.commit)
        self.assertEqual(timeout, 8)
        self.assertIsNone(verify.NoRedirect().redirect_request(request, None, 302, "Found", {}, "https://evil.example"))

    def test_http_error_and_duplicate_json_fail_closed(self):
        class Opener:
            def open(self, *args, **kwargs):
                raise HTTPError("https://api.github.com", 403, "Forbidden", {}, None)
        with patch.object(verify, "build_opener", return_value=Opener()):
            with self.assertRaisesRegex(TaskError, "github_http_403"):
                verify._get("/git/commits/" + self.commit)
        with self.assertRaises(ValueError):
            json.loads('{"x": 1, "x": 2}', object_pairs_hook=verify._pairs)

    def test_capability_opt_in_only(self):
        from msw_engine import tasks
        pack = types.ModuleType("msw_engine.runtime_pack")
        pack.module_root = lambda name: None
        private = types.ModuleType("msw_engine.private_adapters")
        private.available_handlers = lambda: {}
        native = types.ModuleType("msw_engine.native_autonomy")
        native.execute = lambda *args: None
        own = types.ModuleType("msw_engine.own_model")
        own.available_handlers = lambda: {}
        modules = {"msw_engine.runtime_pack": pack, "msw_engine.private_adapters": private,
                   "msw_engine.native_autonomy": native, "msw_engine.own_model": own}
        with patch.dict("sys.modules", modules), patch.dict(os.environ, {}, clear=True):
            self.assertNotIn("github.verify_file", tasks.handlers())
            os.environ["MSW_GITHUB_VERIFY_ENABLED"] = "1"
            self.assertIs(tasks.handlers()["github.verify_file"], verify.handle)
            os.environ["MSW_RUNTIME_MODE"] = "render-free"
            self.assertNotIn("github.verify_file", tasks.handlers())


if __name__ == "__main__":
    unittest.main()
