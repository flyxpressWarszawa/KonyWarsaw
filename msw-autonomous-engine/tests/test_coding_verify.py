"""Offline contract tests. Synthetic API responses are not a production ACK."""
import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from msw_engine import coding_verify as coding
from msw_engine import github_verify as github
from msw_engine.client import TransportError, encode
from msw_engine.runner import Worker
from msw_engine import runner
from msw_engine.runner import child_task as actual_child_task
from msw_engine.tasks import TaskError

FIXTURE = Path(__file__).parent / "fixtures" / "coding-task-utf8.json"


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def isolated_coding_child(connection, kind, payload, directory, max_seconds, memory_mb):
    # Real spawn/dispatch/process limits; only GitHub HTTP is replaced here.
    fixture = CodingDeliveryTests()
    fixture.setUp()
    responses = fixture.responses(fixture.raw)
    with patch.object(github, "_get", side_effect=lambda path: responses[path]):
        actual_child_task(connection, kind, payload, directory, max_seconds, memory_mb)


class CodingDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.raw = FIXTURE.read_bytes()
        self.task = json.loads(self.raw)
        self.commit, self.root, self.subtree = "a" * 40, "b" * 40, "c" * 40
        self.request = {
            "schema": coding.SCHEMA, "request_id": "coding-verify-20261107-001",
            "repository": github.REPOSITORY, "queue_branch": coding.QUEUE_BRANCH,
            "task_id": self.task["id"], "request_sha256": self.task["request_sha256"],
            "commit_sha": self.commit, "task_path": "msw-coding-tasks/" + self.task["id"] + ".json",
            "file_sha256": hashlib.sha256(self.raw).hexdigest(),
        }
        self.calls = []

    def responses(self, raw):
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
        return {
            "/git/commits/" + self.commit: {"sha": self.commit, "tree": {"sha": self.root}},
            "/git/trees/" + self.root: {"sha": self.root, "truncated": False, "tree": [
                {"path": "msw-coding-tasks", "mode": "040000", "type": "tree", "sha": self.subtree}]},
            "/git/trees/" + self.subtree: {"sha": self.subtree, "truncated": False, "tree": [
                {"path": self.request["task_id"] + ".json", "mode": "100644", "type": "blob", "sha": blob}]},
            "/git/blobs/" + blob: {"sha": blob, "encoding": "base64", "size": len(raw),
                                   "content": base64.b64encode(raw).decode("ascii")},
            "/git/ref/heads/" + coding.QUEUE_BRANCH: {"ref": "refs/heads/" + coding.QUEUE_BRANCH,
                                                    "object": {"type": "commit", "sha": self.commit}},
        }

    def execute(self, raw=None, responses=None):
        raw = self.raw if raw is None else raw
        values = self.responses(raw) if responses is None else responses
        def get(path):
            self.calls.append(path)
            return values[path]
        with patch.object(github, "_get", side_effect=get):
            return coding.handle(self.request, Path("."))

    def change_task(self, **values):
        task = {**self.task, **values}
        task.pop("request_sha256", None)
        task["request_sha256"] = hashlib.sha256(canonical(task)).hexdigest()
        self.request["request_sha256"] = task["request_sha256"]
        self.raw = canonical(task) + b"\n"
        self.request["file_sha256"] = hashlib.sha256(self.raw).hexdigest()

    def test_shared_utf8_fixture_and_separate_file_and_request_hashes(self):
        self.assertEqual(self.task["request_sha256"], "c5260202d2428fc1db05ae50bc5e5461fb835fb64ba9d9ac69f1ebd72800b17a")
        self.assertNotEqual(self.request["file_sha256"], self.task["request_sha256"])
        result = self.execute()
        self.assertEqual(result["schema"], coding.RESULT_SCHEMA)
        self.assertEqual(result["request_sha256"], self.task["request_sha256"])
        self.assertEqual(result["file_sha256"], self.request["file_sha256"])
        self.assertEqual(result["queue_head_sha"], self.commit)
        for name in ("github_file_verified", "task_identity_verified", "canonical_task_verified", "queue_branch_membership_verified"):
            self.assertIs(result[name], True)
        for name in ("wordpress_ack_verified", "tests_verified", "code_executed", "deployed_verified"):
            self.assertIs(result[name], False)
        self.assertNotIn(self.task["objective"], json.dumps(result, ensure_ascii=False))

    def test_transport_receipt_mapping_ignores_unrelated_fields_and_does_not_enqueue(self):
        receipt = {"schema": "msw.coding-bridge.receipt.v1", "state": "delivered",
                   **{key: self.request[key] for key in coding.BOUND_KEYS}, "untrusted_url": "https://invalid.example"}
        with patch.object(github, "_get") as get:
            self.assertEqual(coding.verification_request(receipt, self.request["request_id"]), self.request)
            receipt["state"] = "stored_unacknowledged"
            self.assertEqual(coding.verification_request(receipt, self.request["request_id"]), self.request)
            get.assert_not_called()
        for state in ("not_started", "blocked", "write_unconfirmed", "idle", {}, []):
            with self.subTest(state=state), self.assertRaisesRegex(TaskError, "coding_transport_receipt_unconfirmed"):
                coding.verification_request({**receipt, "state": state}, self.request["request_id"])

    def test_missing_file_hash_cannot_be_substituted_by_request_hash(self):
        receipt = {"schema": "msw.coding-bridge.receipt.v1", "state": "delivered",
                   **{key: self.request[key] for key in coding.BOUND_KEYS if key != "file_sha256"}}
        with self.assertRaisesRegex(TaskError, "coding_invalid_verification_identity"):
            coding.verification_request(receipt, self.request["request_id"])
        self.request["file_sha256"] = self.request["request_sha256"]
        with self.assertRaisesRegex(TaskError, "github_expected_sha256_mismatch"):
            self.execute()

    def test_request_schema_and_destinations_fail_before_network(self):
        cases = [dict(self.request, url="https://invalid.example"), dict(self.request, schema="other"),
                 dict(self.request, repository="someone/repo"), dict(self.request, queue_branch="main"),
                 dict(self.request, task_path="msw-coding-tasks/../secrets.json"),
                 dict(self.request, commit_sha="abc123"), dict(self.request, file_sha256=True),
                 dict(self.request, request_id="short"), dict(self.request, task_id="z" * 32)]
        with patch.object(github, "_get") as get:
            for value in cases:
                with self.subTest(value=value), self.assertRaises(TaskError):
                    coding.handle(value, Path("."))
            get.assert_not_called()

    def test_only_exact_task_ledger_paths_are_added_to_file_verifier(self):
        job = {"schema": github.SCHEMA, "request_id": self.request["request_id"], "commit_sha": self.commit,
               "path": self.request["task_path"], "expected_sha256": self.request["file_sha256"]}
        self.assertEqual(github._validate(job)[2], self.request["task_path"])
        for path in ("msw-coding-tasks/readme.json", "msw-coding-tasks/" + "a" * 32 + ".py",
                     "msw-coding-tasks/nested/" + "a" * 32 + ".json", "msw-coding-results/" + "a" * 32 + ".json",
                     "msw-coding-bridge/credentials/key.php", "msw-owned-llm/.env.py"):
            with self.subTest(path=path), self.assertRaisesRegex(TaskError, "github_path_not_allowed"):
                github._validate({**job, "path": path})
        for path in ("msw-coding-bridge/wordpress/bridge.php", "msw-owned-llm/server.py"):
            self.assertEqual(github._validate({**job, "path": path})[2], path)

    def test_valid_file_with_wrong_internal_digest_is_rejected(self):
        changed = dict(self.task, objective=self.task["objective"] + " changed")
        self.raw = canonical(changed) + b"\n"
        self.request["file_sha256"] = hashlib.sha256(self.raw).hexdigest()
        with self.assertRaisesRegex(TaskError, "coding_task_digest_mismatch"):
            self.execute()
        self.assertFalse(any(path.startswith("/git/ref/") for path in self.calls))

    def test_task_identity_must_match_request_and_source_derivation(self):
        self.request["request_sha256"] = "d" * 64
        with self.assertRaisesRegex(TaskError, "coding_task_identity_mismatch"):
            self.execute()
        self.change_task(source_engine="python-hub")
        with self.assertRaisesRegex(TaskError, "coding_task_id_mismatch"):
            self.execute()

    def test_public_flag_and_all_required_task_fields_are_enforced(self):
        for value in (False, 1, "true"):
            with self.subTest(value=value):
                self.change_task(public_data=value)
                with self.assertRaisesRegex(TaskError, "coding_task_not_public"):
                    self.execute()
        self.change_task(extra="unknown")
        with self.assertRaisesRegex(TaskError, "coding_invalid_task_schema"):
            self.execute()

    def test_unicode_blank_text_invalid_criteria_and_dates_are_rejected(self):
        cases = [({"title": "\u00a0\u2028"}, "coding_invalid_task_text"),
                 ({"objective": "x" * 4001}, "coding_invalid_task_text"),
                 ({"acceptance": []}, "coding_invalid_acceptance"),
                 ({"acceptance": ["x"] * 9}, "coding_invalid_acceptance"),
                 ({"created_at": "2026-02-30T00:00:00Z"}, "coding_invalid_timestamp"),
                 ({"target": "unapproved"}, "coding_invalid_target")]
        for changed, reason in cases:
            with self.subTest(changed=changed):
                self.change_task(**changed)
                with self.assertRaisesRegex(TaskError, reason):
                    self.execute()

    def test_duplicate_json_nonfinite_pretty_print_and_extra_newline_fail(self):
        bad = [(b'{"schema":"duplicate",' + self.raw[1:], "coding_invalid_task_json"),
               (self.raw.replace(b'"public_data":true', b'"public_data":NaN'), "coding_invalid_task_json"),
               (json.dumps(self.task, ensure_ascii=False, indent=2).encode() + b"\n", "coding_task_not_canonical"),
               (self.raw + b"\n", "coding_task_not_canonical")]
        for raw, reason in bad:
            with self.subTest(reason=reason):
                self.request["file_sha256"] = hashlib.sha256(raw).hexdigest()
                with self.assertRaisesRegex(TaskError, reason):
                    self.execute(raw)

    def test_known_secret_pattern_is_not_transported_as_public_requirement(self):
        self.change_task(objective="Do not publish ghp_" + "X" * 36)
        with self.assertRaisesRegex(TaskError, "coding_secret_like_content"):
            self.execute()

    def test_task_size_limit_is_stricter_than_generic_file_limit(self):
        raw = b" " * (coding.MAX_TASK + 1)
        self.request["file_sha256"] = hashlib.sha256(raw).hexdigest()
        with self.assertRaisesRegex(TaskError, "github_blob_not_verified"):
            self.execute(raw)

    def test_ancestor_commit_is_verified_against_fresh_queue_head(self):
        values = self.responses(self.raw)
        head = "d" * 40
        values["/git/ref/heads/" + coding.QUEUE_BRANCH]["object"]["sha"] = head
        compare_path = "/compare/" + self.commit + "..." + head + "?per_page=1"
        values[compare_path] = {"status": "ahead", "base_commit": {"sha": self.commit},
                                "merge_base_commit": {"sha": self.commit}, "ahead_by": 7, "behind_by": 0}
        result = self.execute(responses=values)
        self.assertEqual(result["queue_head_sha"], head)
        self.assertEqual(self.calls[-1], compare_path)

    def test_unrelated_diverged_or_incomplete_branch_evidence_is_rejected(self):
        for change in ({"status": "diverged"}, {"status": "behind"}, {"merge_base_commit": {"sha": "e" * 40}},
                       {"base_commit": []}, {"ahead_by": True}, {"behind_by": False}):
            values = self.responses(self.raw)
            head = "d" * 40
            values["/git/ref/heads/" + coding.QUEUE_BRANCH]["object"]["sha"] = head
            values["/compare/" + self.commit + "..." + head + "?per_page=1"] = {
                "status": "ahead", "base_commit": {"sha": self.commit}, "merge_base_commit": {"sha": self.commit},
                "ahead_by": 1, "behind_by": 0, **change}
            with self.subTest(change=change), self.assertRaisesRegex(TaskError, "coding_commit_not_on_queue_branch"):
                self.execute(responses=values)

    def test_branch_ref_cannot_be_substituted_or_redirected_by_task(self):
        values = self.responses(self.raw)
        values["/git/ref/heads/" + coding.QUEUE_BRANCH]["ref"] = "refs/heads/main"
        with self.assertRaisesRegex(TaskError, "coding_queue_ref_not_verified"):
            self.execute(responses=values)

    def test_failed_git_read_or_changed_file_never_produces_delivery_proof(self):
        with patch.object(github, "_get", side_effect=TaskError("github_http_403")):
            with self.assertRaisesRegex(TaskError, "github_http_403"):
                coding.handle(self.request, Path("."))
        self.request["file_sha256"] = "d" * 64
        with self.assertRaisesRegex(TaskError, "github_expected_sha256_mismatch"):
            self.execute()

    def test_existing_journal_waits_for_complete_and_independent_status(self):
        result = {"ok": True, "data": self.execute()}
        payload = encode(self.request)
        job = {"id": "4c2bebd7-d514-4c8f-bb55-777777777777", "kind": "coding.verify_delivery",
               "payload": payload.decode(), "payload_sha256": hashlib.sha256(payload).hexdigest()}
        calls = []
        class SyntheticClient:
            def __init__(self): self.wrong_status = True
            def send(self, action, value):
                calls.append((action, deepcopy(value)))
                if action == "complete":
                    return {"stored": True, "result_sha256": value["result_sha256"]}
                if action == "status":
                    return {"state": "completed", "result_sha256": "0" * 64 if self.wrong_status else receipt["result_sha256"]}
                raise AssertionError(action)
        with tempfile.TemporaryDirectory() as folder:
            client = SyntheticClient()
            worker = Worker(client, Path(folder))
            receipt = worker.journal.save(job, "synthetic-lease", result)
            with self.assertRaisesRegex(TransportError, "result_readback_mismatch"):
                worker.acknowledge(receipt)
            self.assertEqual(worker.journal.lookup(job["id"], job["payload_sha256"])["state"], "pending")
            client.wrong_status = False
            self.assertTrue(worker.acknowledge(receipt)["server_ack_verified"])
            saved = worker.journal.lookup(job["id"], job["payload_sha256"])
            self.assertEqual(saved["result_json"], receipt["result_json"])
            self.assertEqual([action for action, _ in calls], ["complete", "status", "complete", "status"])
            self.assertTrue(all(value["job_id"] == job["id"] for _, value in calls))
            self.assertEqual(saved["state"], "acknowledged")
            self.assertFalse(json.loads(saved["result_json"])["data"]["wordpress_ack_verified"])

    def test_new_handler_is_disabled_by_default_and_in_render_free_mode(self):
        from msw_engine import tasks
        pack = types.ModuleType("msw_engine.runtime_pack"); pack.module_root = lambda name: None
        private = types.ModuleType("msw_engine.private_adapters"); private.available_handlers = lambda: {}
        native = types.ModuleType("msw_engine.native_autonomy"); native.execute = lambda *args: None
        own = types.ModuleType("msw_engine.own_model"); own.available_handlers = lambda: {}
        modules = {"msw_engine.runtime_pack": pack, "msw_engine.private_adapters": private,
                   "msw_engine.native_autonomy": native, "msw_engine.own_model": own}
        with patch.dict("sys.modules", modules), patch.dict(os.environ, {}, clear=True):
            self.assertNotIn("coding.verify_delivery", tasks.handlers())
            os.environ["MSW_GITHUB_VERIFY_ENABLED"] = "1"
            self.assertIs(tasks.handlers()["coding.verify_delivery"], coding.handle)
            os.environ["MSW_RUNTIME_MODE"] = "render-free"
            self.assertNotIn("coding.verify_delivery", tasks.handlers())


class CodingWorkerTests(unittest.TestCase):
    def worker_round_trip(self, changed_digest=False):
        fixture = CodingDeliveryTests()
        fixture.setUp()
        payload = deepcopy(fixture.request)
        if changed_digest:
            payload["request_sha256"] = "d" * 64
        raw = encode(payload)
        job = {"id": "synthetic-coding-job-001", "kind": "coding.verify_delivery", "payload": raw.decode(),
               "payload_sha256": hashlib.sha256(raw).hexdigest()}
        class SyntheticQueue:
            result = None
            result_sha256 = None
            def send(self, action, value):
                if action == "claim":
                    if "coding.verify_delivery" not in value["capabilities"]:
                        raise AssertionError("new handler not advertised")
                    return {"state": "claimed", "job": job, "lease_token": "synthetic-lease"}
                if value.get("job_id") != job["id"]:
                    raise AssertionError("native job identity changed")
                if action == "complete":
                    if value["payload_sha256"] != job["payload_sha256"] or value["lease_token"] != "synthetic-lease":
                        raise AssertionError("native request or lease changed")
                    self.result_sha256 = hashlib.sha256(value["result_json"].encode()).hexdigest()
                    if self.result_sha256 != value["result_sha256"]:
                        raise AssertionError("result bytes changed")
                    self.result = json.loads(value["result_json"])
                    return {"stored": True, "result_sha256": self.result_sha256}
                if action == "status":
                    return {"state": "completed" if self.result["ok"] else "failed", "result_sha256": self.result_sha256}
                raise AssertionError(action)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
                "MSW_GITHUB_VERIFY_ENABLED": "1", "MSW_AUTONOMOUS_AGENT": "0"}, clear=True):
            queue = SyntheticQueue()
            worker = Worker(queue, Path(folder), max_seconds=15, memory_mb=0)
            with patch.object(runner, "child_task", isolated_coding_child):
                outcome = worker.run_once()
            receipt = worker.journal.lookup(job["id"], job["payload_sha256"])
            self.assertEqual(receipt["state"], "acknowledged")
            self.assertEqual(receipt["result_sha256"], queue.result_sha256)
            return outcome, queue.result

    def test_new_handler_runs_in_real_child_and_existing_native_journal(self):
        outcome, result = self.worker_round_trip()
        self.assertEqual(outcome["state"], "completed")
        self.assertIs(outcome["server_ack_verified"], True)
        self.assertIs(result["data"]["task_identity_verified"], True)
        self.assertIs(result["data"]["wordpress_ack_verified"], False)
        self.assertIs(result["data"]["code_executed"], False)

    def test_native_failure_receipt_is_not_successful_task_delivery(self):
        outcome, result = self.worker_round_trip(changed_digest=True)
        self.assertEqual(outcome["state"], "failed")
        self.assertEqual(result, {"ok": False, "error": "coding_task_identity_mismatch"})


if __name__ == "__main__":
    unittest.main()
