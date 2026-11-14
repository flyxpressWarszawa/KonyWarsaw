"""Offline regressions; live tests require a separate explicitly pinned manifest."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from msw_engine import python_task_pilot as p


def document():
    return {"schema": p.SCHEMA, "classification": "synthetic_public_test", "tasks": [
        {"task_id": "py10-20261114-seo", "kind": "seo.analyze", "payload": {
            "title": "Test SEO", "content": "alpha beta alpha", "html": '<h2>Test</h2><img alt="ok"><a href="/a">a</a>', "url": "https://example.org/"}},
        {"task_id": "py10-20261114-rank", "kind": "research.rank", "payload": {"ideas": [
            {"id": "fixture-a", "title": "Synthetic A", "demand": 80, "competition": 20, "margin": 70, "delivery_effort": 30, "sources": ["https://example.org/"]}]}}
    ]}


class Store:
    def __init__(self, doc=None):
        self.files = {p.REQUEST_PATH: p.wire(document() if doc is None else doc)}
        self.puts = []
        self.failure = None

    def get(self, path):
        return self.files.get(path)

    def create(self, path, raw):
        self.puts.append(path)
        if self.failure == "before":
            raise p.PilotError("transport_uncertain")
        if path in self.files:
            raise AssertionError("must not overwrite")
        self.files[path] = raw if self.failure != "corrupt" else b"corrupt"
        if self.failure == "after":
            self.failure = None
            raise p.PilotError("transport_uncertain")


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "private"
        self.store = Store()
        self.calls = []

    def runner(self, task, root):
        self.calls.append(task["task_id"])
        return {"computed": task["kind"]}

    def run_pilot(self, runner=None):
        return p.run_batch(self.store, self.root, p.sha(self.store.files[p.REQUEST_PATH]), runner or self.runner)

    def test_two_jobs_execute_in_order_and_digests_match(self):
        out = self.run_pilot()
        self.assertEqual(self.calls, ["py10-20261114-seo", "py10-20261114-rank"])
        self.assertEqual(len(self.store.puts), 2)
        self.assertFalse(out["central_hermes_ack_verified"])
        for row in out["tasks"]:
            raw = self.store.files[p.result_path(row["task_id"])]
            self.assertEqual(row["receipt_sha256"], p.sha(raw))
            record = json.loads(raw)
            self.assertEqual(record["result_sha256"], p.sha(p.wire(record["output"])))
            self.assertEqual(record["provider_calls"], 0)

    def test_restart_reuses_both_results_without_execution_or_put(self):
        first = self.run_pilot()
        second = self.run_pilot()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.store.puts), 2)
        self.assertTrue(all(x["reused"] for x in second["tasks"]))
        self.assertEqual([x["receipt_sha256"] for x in first["tasks"]], [x["receipt_sha256"] for x in second["tasks"]])

    def test_wrong_digest_never_creates_storage(self):
        with self.assertRaisesRegex(p.PilotError, "manifest_digest"):
            p.run_batch(self.store, self.root, "0" * 64, self.runner)
        self.assertFalse(self.root.exists())

    def test_unknown_operation_rejected(self):
        d = document(); d["tasks"][0]["kind"] = "shell.execute"
        raw = p.wire(d)
        with self.assertRaisesRegex(p.PilotError, "manifest_contract"):
            p.validate_manifest(raw, p.sha(raw))

    def test_duplicate_job_ids_rejected(self):
        d = document(); d["tasks"][1]["task_id"] = d["tasks"][0]["task_id"]
        raw = p.wire(d)
        with self.assertRaises(p.PilotError):
            p.validate_manifest(raw, p.sha(raw))

    def test_nonpublic_classification_rejected(self):
        d = document(); d["classification"] = "customer_private"
        raw = p.wire(d)
        with self.assertRaises(p.PilotError):
            p.validate_manifest(raw, p.sha(raw))

    def test_extra_fields_and_bad_json_rejected(self):
        d = document(); d["command"] = "do not run"
        for raw in (p.wire(d), b"not-json", b"null", b"[]"):
            with self.subTest(raw=raw[:20]), self.assertRaises(p.PilotError):
                p.validate_manifest(raw, p.sha(raw))

    def test_oversize_manifest_rejected(self):
        raw = b"x" * (p.MAX_BYTES + 1)
        with self.assertRaises(p.PilotError):
            p.validate_manifest(raw, p.sha(raw))

    def test_idempotency_conflict_not_reexecuted(self):
        self.run_pilot()
        d = document(); d["tasks"][0]["payload"]["title"] = "changed"
        self.store.files[p.REQUEST_PATH] = p.wire(d)
        with self.assertRaisesRegex(p.PilotError, "task_conflict"):
            self.run_pilot()
        self.assertEqual(len(self.calls), 2)

    def test_crashed_execution_is_not_reissued(self):
        def broken(task, root):
            self.calls.append(task["task_id"])
            raise RuntimeError("simulated crash")
        with self.assertRaises(RuntimeError):
            self.run_pilot(broken)
        with self.assertRaisesRegex(p.PilotError, "execution_uncertain_no_retry"):
            self.run_pilot()
        self.assertEqual(len(self.calls), 1)

    def test_lost_reply_after_write_reconciles_without_second_put(self):
        self.store.failure = "after"
        with self.assertRaises(p.PilotError):
            self.run_pilot()
        out = self.run_pilot()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.store.puts), 2)
        self.assertTrue(out["tasks"][0]["reused"])

    def test_unknown_write_missing_on_readback_never_retries(self):
        self.store.failure = "before"
        with self.assertRaises(p.PilotError):
            self.run_pilot()
        self.store.failure = None
        with self.assertRaisesRegex(p.PilotError, "publication_uncertain_no_retry"):
            self.run_pilot()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.store.puts), 1)

    def test_existing_conflicting_remote_receipt_is_not_overwritten(self):
        self.store.files[p.result_path("py10-20261114-seo")] = b"unrelated"
        with self.assertRaisesRegex(p.PilotError, "remote_receipt_conflict"):
            self.run_pilot()
        self.assertEqual(self.store.puts, [])

    def test_bad_readback_is_not_success(self):
        self.store.failure = "corrupt"
        with self.assertRaisesRegex(p.PilotError, "remote_readback_mismatch"):
            self.run_pilot()

    def test_corrupted_local_result_is_not_trusted(self):
        self.run_pilot()
        with sqlite3.connect(self.root / "pilot.sqlite3") as db:
            db.execute("UPDATE jobs SET receipt=? WHERE id=?", (b'{}', "py10-20261114-seo"))
        with self.assertRaisesRegex(p.PilotError, "local_receipt_integrity"):
            self.run_pilot()

    def test_concurrent_worker_cannot_execute_running_task(self):
        entered = threading.Event(); release = threading.Event()
        def slow(task, root):
            self.calls.append(task["task_id"])
            if len(self.calls) == 1:
                entered.set(); release.wait(3)
            return {"ok": True}
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.run_pilot, slow)
            self.assertTrue(entered.wait(3))
            try:
                with self.assertRaisesRegex(p.PilotError, "execution_uncertain_no_retry"):
                    self.run_pilot()
            finally:
                release.set()
            first.result()
        self.assertEqual(len(self.calls), 2)

    def test_storage_symlink_blocked(self):
        self.root.symlink_to(Path(self.tmp.name), target_is_directory=True)
        with self.assertRaisesRegex(p.PilotError, "storage_symlink"):
            self.run_pilot()

    def test_unapproved_transport_paths_rejected_before_network(self):
        for path in ("README.md", "knowledge/hermes-chat-loop/github-queue.json", "../secrets"):
            with self.assertRaisesRegex(p.PilotError, "path_or_method"):
                p.GitHubStore().get(path)
        with self.assertRaisesRegex(p.PilotError, "path_or_method"):
            p.GitHubStore().create(p.REQUEST_PATH, b"change")

    def test_live_mode_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(p.run_live_once()["state"], "disabled")

    def test_production_service_rejected(self):
        with patch.dict(os.environ, {"MSW_HERMES_PYTHON_TASK_PILOT": "1", "RENDER_SERVICE_ID": "production"}, clear=True):
            self.assertEqual(p.run_live_once()["state"], "wrong_service_blocked")

    def test_ephemeral_disk_rejected(self):
        with patch.dict(os.environ, {"MSW_HERMES_PYTHON_TASK_PILOT": "1", "RENDER_SERVICE_ID": p.SERVICE}, clear=True), patch("os.path.ismount", return_value=False):
            self.assertEqual(p.run_live_once()["state"], "persistent_disk_not_verified")

    @unittest.skipUnless((Path(p.__file__).parent / "tasks.py").exists(), "Full repository handler test runs in GitHub CI")
    def test_actual_repository_handlers(self):
        self.run_pilot(p.execute)
        seo = json.loads(self.store.files[p.result_path("py10-20261114-seo")])["output"]
        rank = json.loads(self.store.files[p.result_path("py10-20261114-rank")])["output"]
        self.assertEqual(seo["word_count"], 3)
        self.assertEqual(seo["headings"], {"h2": 1})
        self.assertEqual(rank["ideas"][0]["score"], 76.0)


if __name__ == "__main__":
    unittest.main()
