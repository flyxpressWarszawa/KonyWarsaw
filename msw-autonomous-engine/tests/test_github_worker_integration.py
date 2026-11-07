"""Offline contracts across the real worker, GitHub verifier and journal.

The GitHub API and WordPress queue are explicit fixtures; this is not a live
transport or proof of a production grant. The worker still uses spawn and its
real process, payload hash, SQLite journal and acknowledgement/readback logic.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from msw_engine import github_verify, runner
from msw_engine.client import TransportError
from msw_engine.runner import child_task as actual_child_task


COMMIT = "a" * 40
ROOT = "b" * 40
SUBTREE = "c" * 40
CONTENT = b"print('MSW')\n"
BLOB = hashlib.sha1(b"blob " + str(len(CONTENT)).encode() + b"\0" + CONTENT).hexdigest()
CONTENT_SHA = hashlib.sha256(CONTENT).hexdigest()
RESPONSES = {
    "/git/commits/" + COMMIT: {"sha": COMMIT, "tree": {"sha": ROOT}},
    "/git/trees/" + ROOT: {"sha": ROOT, "truncated": False, "tree": [
        {"path": "msw-autonomous-engine", "mode": "040000", "type": "tree", "sha": SUBTREE}]},
    "/git/trees/" + SUBTREE: {"sha": SUBTREE, "truncated": False, "tree": [
        {"path": "check.py", "mode": "100644", "type": "blob", "sha": BLOB}]},
    "/git/blobs/" + BLOB: {"sha": BLOB, "encoding": "base64", "size": len(CONTENT),
                           "content": base64.b64encode(CONTENT).decode()},
}


def isolated_child(connection, kind, payload, directory, max_seconds, memory_mb):
    # This function is imported again by multiprocessing's actual spawn method.
    # Only HTTP is replaced; dispatch and the tested verifier are real code.
    with patch.object(github_verify, "_get", side_effect=lambda path: RESPONSES[path]):
        actual_child_task(connection, kind, payload, directory, max_seconds, memory_mb)


def forbidden_child(*args):
    raise AssertionError("cached job must not execute twice")


class FakeQueue:
    def __init__(self, job):
        self.job = job
        self.result = None
        self.digest = None
        self.completions = 0
        self.wrong_readback = False

    def send(self, action, payload):
        if action == "claim":
            if "github.verify_file" not in payload["capabilities"]:
                raise AssertionError("verifier capability was not advertised")
            return {"state": "claimed", "job": self.job, "lease_token": "lease-fixture"}
        if action == "complete":
            self.completions += 1
            raw = payload["result_json"].encode()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != payload["result_sha256"]:
                raise AssertionError("worker sent mismatched receipt bytes")
            self.result, self.digest = json.loads(raw), digest
            return {"stored": True, "result_sha256": digest}
        if action == "status":
            return {"state": "completed" if self.result["ok"] else "failed",
                    "result_sha256": "0" * 64 if self.wrong_readback else self.digest}
        raise AssertionError("unexpected action: " + action)


def build_job(expected_sha=CONTENT_SHA):
    payload = {"schema": github_verify.SCHEMA, "request_id": "msw-integration-1234", "commit_sha": COMMIT,
               "path": "msw-autonomous-engine/check.py", "expected_sha256": expected_sha}
    raw = json.dumps(payload, separators=(",", ":"))
    return {"id": "fixture-job-0001", "kind": "github.verify_file", "payload": raw,
            "payload_sha256": hashlib.sha256(raw.encode()).hexdigest()}


class GithubWorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"MSW_GITHUB_VERIFY_ENABLED": "1", "MSW_AUTONOMOUS_AGENT": "0"}, clear=True)
        self.env.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.env.stop)

    def worker(self, job):
        server = FakeQueue(job)
        return runner.Worker(server, Path(self.tmp.name), max_seconds=15, memory_mb=0), server

    def test_verified_file_goes_through_claim_journal_and_readback(self):
        job = build_job()
        worker, server = self.worker(job)
        with patch.object(runner, "child_task", isolated_child):
            result = worker.run_once()
        self.assertEqual(result, {"job_id": job["id"], "state": "completed", "server_ack_verified": True})
        self.assertTrue(server.result["ok"])
        data = server.result["data"]
        self.assertTrue(data["github_file_verified"])
        self.assertEqual((data["commit_sha"], data["content_sha256"]), (COMMIT, CONTENT_SHA))
        self.assertFalse(data["tests_verified"])
        self.assertFalse(data["deployed_verified"])
        self.assertEqual(worker.journal.lookup(job["id"], job["payload_sha256"])["state"], "acknowledged")

    def test_hash_mismatch_is_a_receipted_failure_not_a_success(self):
        job = build_job("d" * 64)
        worker, server = self.worker(job)
        with patch.object(runner, "child_task", isolated_child):
            result = worker.run_once()
        self.assertEqual(result["state"], "failed")
        self.assertTrue(result["server_ack_verified"])
        self.assertEqual(server.result, {"ok": False, "error": "github_expected_sha256_mismatch"})

    def test_wrong_server_readback_keeps_the_receipt_pending(self):
        job = build_job()
        worker, server = self.worker(job)
        server.wrong_readback = True
        with patch.object(runner, "child_task", isolated_child):
            with self.assertRaisesRegex(TransportError, "result_readback_mismatch"):
                worker.run_once()
        self.assertEqual(worker.journal.lookup(job["id"], job["payload_sha256"])["state"], "pending")
        server.wrong_readback = False
        self.assertEqual(worker.replay_receipts(), 1)
        self.assertEqual(worker.journal.lookup(job["id"], job["payload_sha256"])["state"], "acknowledged")

    def test_changed_payload_is_rejected_before_acknowledgement(self):
        job = build_job()
        job["payload_sha256"] = "0" * 64
        worker, server = self.worker(job)
        with patch.object(runner, "child_task", isolated_child):
            with self.assertRaisesRegex(TransportError, "job_payload_hash_mismatch"):
                worker.run_once()
        self.assertEqual(server.completions, 0)

    def test_duplicate_job_reuses_the_existing_journal_result(self):
        job = build_job()
        worker, _ = self.worker(job)
        with patch.object(runner, "child_task", isolated_child):
            first = worker.execute(job, "lease-one")
        with patch.object(runner, "child_task", forbidden_child):
            second = worker.execute(job, "lease-two")
        self.assertEqual(first["result_sha256"], second["result_sha256"])
        self.assertEqual(first["result_json"], second["result_json"])
        self.assertEqual(second["lease_token"], "lease-two")

    def test_disabled_capability_rejects_the_job(self):
        job = build_job()
        worker, _ = self.worker(job)
        os.environ.pop("MSW_GITHUB_VERIFY_ENABLED")
        with self.assertRaisesRegex(TransportError, "job_capability_mismatch"):
            worker.execute(job, "lease-fixture")


if __name__ == "__main__":
    unittest.main()
