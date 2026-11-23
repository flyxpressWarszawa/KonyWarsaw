"""Offline tests for Codex SQLite report transport; native Hermes ACK is separate."""
from contextlib import closing
import hashlib
import json
import os
import importlib.util
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from unittest import mock

from msw_engine.hermes_chat_bridge import SCHEMA, handle
from msw_engine import hermes_chat_bridge as bridge

ADAPTER = Path(__file__).resolve().parents[1] / "integrations" / "hermes_codex_native_v1" / "sqlite_apply_adapter.py"
spec = importlib.util.spec_from_file_location("sqlite_apply_adapter", ADAPTER)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def receipt(text, job):
    obj = {"status": "VERIFIED", "independent_readback": True, "tools": 0,
           "text": text, "response_sha256": hashlib.sha256(text.encode()).hexdigest()}
    obj.update({key: job[key] for key in ("root_id", "task_id", "request_id", "work_key", "parent_id")})
    obj["receipt_sha256"] = hashlib.sha256(module.canonical(obj).encode()).hexdigest()
    return obj


class SQLiteApplyAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.job = {"root_id": "root-00000001", "task_id": "task-00000001",
                    "request_id": "request-00000001", "work_key": "work-00000001",
                    "parent_id": "root-00000001"}
        handle({"schema": SCHEMA, "operation": "submit",
                "task_id": self.job["root_id"], "conversation_key": "conv-00000001",
                "kind": "plan", "goal": "Acknowledged parent", "executor": "hermes",
                "work_key": "work-root-00000001"}, self.directory)
        parent_lease = handle({"schema": SCHEMA, "operation": "claim",
                               "executor": "hermes", "conversation_key": "conv-00000001"},
                              self.directory)["lease_token"]
        parent_result = "parent complete"
        parent_digest = hashlib.sha256(parent_result.encode()).hexdigest()
        handle({"schema": SCHEMA, "operation": "report", "task_id": self.job["root_id"],
                "lease_token": parent_lease, "result": parent_result,
                "result_sha256": parent_digest, "outcome": "completed"}, self.directory)
        handle({"schema": SCHEMA, "operation": "ack", "task_id": self.job["root_id"],
                "result_sha256": parent_digest}, self.directory)
        submitted = handle({"schema": SCHEMA, "operation": "submit",
                "task_id": self.job["task_id"], "conversation_key": "conv-00000001",
                "kind": "plan", "goal": "Offline test", "executor": "hermes",
                "work_key": self.job["work_key"], "parent_id": self.job["parent_id"]},
               self.directory)
        # Capture the actual persisted submit contract, not caller-invented
        # equivalents of request SHA / executor / mailbox.
        self.job.update(conversation_key=submitted["conversation_key"],
                        executor=submitted["executor"],
                        request_sha256=submitted["request_sha256"])
        self.lease = handle({"schema": SCHEMA, "operation": "claim",
                             "executor": "hermes", "conversation_key": "conv-00000001"},
                            self.directory)["lease_token"]

    def test_storage_report_readback_does_not_pretend_native_ack(self):
        result = module.apply_once(self.directory, self.job, self.lease, receipt("ok", self.job))
        self.assertEqual(result["status"], "STORAGE_REPORT_VERIFIED_NATIVE_UNVERIFIED")
        self.assertTrue(result["storage_readback_verified"])
        self.assertEqual(result["storage_state"], "reported")
        self.assertFalse(result["native_hermes_ack_verified"])
        self.assertFalse(result["native_application_verified"])
        self.assertNotIn("native_ack", result)
        state = handle({"schema": SCHEMA, "operation": "status",
                        "task_id": self.job["task_id"]}, self.directory)
        self.assertEqual(state["state"], "reported")


    def test_original_sqlite_submit_origin_must_match_before_report(self):
        # Exactly the original persisted submit request, mailbox and executor
        # may be reported. A forged but internally matching Codex receipt
        # cannot substitute a second caller-supplied origin.
        for key, bad in (("request_sha256", "0" * 64),
                         ("conversation_key", "other-conv-0001"),
                         ("executor", "chatgpt_scheduled")):
            for route in ("altered", "missing"):
                with self.subTest(key=key, route=route):
                    changed = dict(self.job)
                    if route == "missing":
                        changed.pop(key)
                        reason = "origin_binding_required"
                    else:
                        changed[key] = bad
                        reason = "persisted_origin_mismatch"
                    with self.assertRaisesRegex(module.ApplyHold, reason):
                        module.apply_once(self.directory, changed, self.lease,
                                          receipt("ok", changed))
                    state = handle({"schema": SCHEMA, "operation": "status",
                                    "task_id": self.job["task_id"]}, self.directory)
                    self.assertEqual(state["state"], "leased")
                    self.assertIsNone(state["result_sha256"])

    def test_wrong_work_key_does_not_write(self):
        changed_job = dict(self.job, work_key="wrong-00000001")
        with self.assertRaisesRegex(module.ApplyHold, "work_identity_mismatch"):
            module.apply_once(self.directory, changed_job,
                              self.lease, receipt("ok", changed_job))
        status = handle({"schema": SCHEMA, "operation": "status",
                         "task_id": self.job["task_id"]}, self.directory)
        self.assertEqual(status["state"], "leased")

    def test_wrong_parent_does_not_write(self):
        changed_job = dict(self.job, parent_id="other-parent-00000001")
        with self.assertRaisesRegex(module.ApplyHold, "work_identity_mismatch"):
            module.apply_once(self.directory, changed_job,
                              self.lease, receipt("ok", changed_job))
        status = handle({"schema": SCHEMA, "operation": "status",
                         "task_id": self.job["task_id"]}, self.directory)
        self.assertEqual(status["state"], "leased")

    def test_duplicate_same_receipt_is_idempotent(self):
        first = module.apply_once(self.directory, self.job, self.lease, receipt("ok", self.job))
        second = module.apply_once(self.directory, self.job, self.lease, receipt("ok", self.job))
        self.assertEqual(first["result_sha256"], second["result_sha256"])
        self.assertEqual(second["storage_state"], "reported")
        self.assertFalse(second["native_hermes_ack_verified"])
        self.assertNotIn("native_ack", second)

    def test_expired_lease_holds_without_attempting_report(self):
        # A known expired reservation is not an UNKNOWN report. The native
        # report handler must not be called, and a former owner cannot write
        # after the lease deadline. The already-committed-result branch has
        # separate readback coverage and remains idempotent.
        database = self.directory / "hermes-chat-loop" / "exchange.sqlite3"
        with sqlite3.connect(database) as db:
            db.execute("UPDATE jobs SET lease_until=0 WHERE id=?",
                       (self.job["task_id"],))
        with patch("msw_engine.hermes_chat_bridge.handle",
                   side_effect=AssertionError("expired lease reached report")) as native_report:
            with self.assertRaisesRegex(module.ApplyHold, "lease_expired"):
                module.apply_once(self.directory, self.job, self.lease,
                                  receipt("expired lease result", self.job))
            native_report.assert_not_called()
        state = handle({"schema": SCHEMA, "operation": "status",
                        "task_id": self.job["task_id"]}, self.directory)
        self.assertEqual(state["state"], "leased")
        self.assertIsNone(state["result_sha256"])

    def test_uncertain_committed_report_is_read_back_without_reack(self):
        answer = "ok after uncertain report"
        digest = hashlib.sha256(answer.encode()).hexdigest()
        handle({"schema": SCHEMA, "operation": "report", "task_id": self.job["task_id"],
                "lease_token": self.lease, "result": answer,
                "result_sha256": digest, "outcome": "completed"}, self.directory)
        database = self.directory / "hermes-chat-loop" / "exchange.sqlite3"
        with sqlite3.connect(database) as db:
            db.execute("UPDATE jobs SET lease_until=0 WHERE id=?", (self.job["task_id"],))

        def reject_second_report(payload, directory):
            if payload["operation"] == "report":
                self.fail("A committed report was submitted again")
            return handle(payload, directory)

        with patch("msw_engine.hermes_chat_bridge.handle", side_effect=reject_second_report):
            applied = module.apply_once(self.directory, self.job, self.lease,
                                        receipt(answer, self.job))
        self.assertEqual(applied["storage_state"], "reported")
        self.assertTrue(applied["storage_readback_verified"])
        self.assertFalse(applied["native_hermes_ack_verified"])
        self.assertEqual(applied["result_sha256"], digest)

    def test_cancelled_after_sqlite_report_commit_holds_without_replay(self):
        # asyncio.CancelledError is not an Exception in supported Python.
        # Simulate cancellation AFTER the genuine isolated SQLite transaction
        # committed but BEFORE the report handler returned its response.
        from asyncio import CancelledError
        answer = "persisted despite cancelled transport"
        proof = receipt(answer, self.job)
        calls = []

        def commit_then_cancel(payload, directory):
            self.assertEqual(payload["operation"], "report")
            calls.append(payload["task_id"])
            handle(payload, directory)
            raise CancelledError()

        with patch("msw_engine.hermes_chat_bridge.handle",
                   side_effect=commit_then_cancel) as routed:
            with self.assertRaisesRegex(module.ApplyHold, "report_unknown_outcome"):
                module.apply_once(self.directory, self.job, self.lease, proof)
            routed.assert_called_once()
        self.assertEqual(calls, [self.job["task_id"]])

        stored = handle({"schema": SCHEMA, "operation": "status",
                         "task_id": self.job["task_id"]}, self.directory)
        self.assertEqual(stored["state"], "reported")
        self.assertEqual(stored["result_sha256"], proof["response_sha256"])

        # A later invocation reconciles exactly this committed row.
        # No second report, native ACK, or Codex dispatch is permitted.
        with patch("msw_engine.hermes_chat_bridge.handle",
                   side_effect=AssertionError("uncertain report replayed")) as routed:
            recovered = module.apply_once(self.directory, self.job, self.lease, proof)
            routed.assert_not_called()
        self.assertEqual(recovered["storage_state"], "reported")
        self.assertTrue(recovered["storage_readback_verified"])
        self.assertFalse(recovered["native_hermes_ack_verified"])
        self.assertFalse(recovered["native_application_verified"])

    def test_uncertain_conflicting_report_stays_unacknowledged(self):
        original = "first result"
        digest = hashlib.sha256(original.encode()).hexdigest()
        handle({"schema": SCHEMA, "operation": "report", "task_id": self.job["task_id"],
                "lease_token": self.lease, "result": original,
                "result_sha256": digest, "outcome": "completed"}, self.directory)
        with self.assertRaisesRegex(module.ApplyHold, "conflicting_recorded_result"):
            module.apply_once(self.directory, self.job, self.lease,
                              receipt("different result", self.job))
        status = handle({"schema": SCHEMA, "operation": "status",
                         "task_id": self.job["task_id"]}, self.directory)
        self.assertEqual(status["state"], "reported")
        self.assertEqual(status["result_sha256"], digest)

    def test_conflicting_second_result_is_rejected(self):
        module.apply_once(self.directory, self.job, self.lease, receipt("ok", self.job))
        with self.assertRaises(Exception):
            module.apply_once(self.directory, self.job, self.lease, receipt("different", self.job))
        state = handle({"schema": SCHEMA, "operation": "status",
                        "task_id": self.job["task_id"]}, self.directory)
        self.assertEqual(state["result_sha256"], hashlib.sha256(b"ok").hexdigest())

    def test_invisible_only_result_rejected_before_sqlite_access(self):
        # A direct SQLite caller bypasses native_apply_bridge. A valid SHA for
        # non-actionable Unicode must not consume the original leased task.
        for raw in ("\u200b", "\ufeff", "\u2060", "\u200e\u200f",
                    "\u200d\u0000", "\t \r\n"):
            with self.subTest(text=repr(raw)):
                proof = receipt(raw, self.job)
                with patch.object(module, "_connection",
                                  side_effect=AssertionError("SQLite accessed")) as connect:
                    with patch("msw_engine.hermes_chat_bridge.handle",
                               side_effect=AssertionError("report submitted")) as send:
                        with self.assertRaisesRegex(module.ApplyHold, "result_invalid"):
                            module.apply_once(self.directory, self.job, self.lease, proof)
                        connect.assert_not_called()
                        send.assert_not_called()
                state = handle({"schema": SCHEMA, "operation": "status",
                                "task_id": self.job["task_id"]}, self.directory)
                self.assertEqual(state["state"], "leased")
                self.assertIsNone(state["result_sha256"])

    def test_noninteger_tools_rejected_before_storage(self):
        # Direct adapter imports must enforce the same exact integer-zero guard
        # as native_apply_bridge; a recomputed receipt digest is not sufficient.
        for unsafe in (False, 0.0, -0.0, True, "0", None):
            with self.subTest(tools=repr(unsafe)):
                forged = receipt("ok", self.job)
                forged["tools"] = unsafe
                forged["receipt_sha256"] = hashlib.sha256(
                    module.canonical({k: v for k, v in forged.items()
                                      if k != "receipt_sha256"}).encode()
                ).hexdigest()
                with patch.object(module, "_connection",
                                  side_effect=AssertionError("SQLite opened")) as connection:
                    with self.assertRaisesRegex(module.ApplyHold, "receipt_unsafe"):
                        module.apply_once(self.directory, self.job, self.lease, forged)
                    connection.assert_not_called()
        status = handle({"schema": SCHEMA, "operation": "status",
                         "task_id": self.job["task_id"]}, self.directory)
        self.assertEqual(status["state"], "leased")
        self.assertIsNone(status.get("result_sha256"))

    def test_coercible_receipt_shapes_hold_before_sqlite(self):
        # json.dumps used to silently normalize int keys and tuples; an
        # attacker could recompute matching SHA over the changed wire form.
        # Reject the original evidence instead of treating that SHA as proof.
        for unsafe in ({"nested": {1: "item"}},
                       {"nested": (1, 2)},
                       {"nested": [{"value": ("a", "b")}]},
                       {"nested": {False: "item"}}):
            with self.subTest(unsafe=repr(unsafe)):
                forged = receipt("ok", self.job)
                forged["extra"] = unsafe
                # Legacy Python JSON encoding accepts/coerces these inputs.
                normalized = json.dumps(
                    {k: v for k, v in forged.items() if k != "receipt_sha256"},
                    sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                    allow_nan=False)
                forged["receipt_sha256"] = hashlib.sha256(normalized.encode()).hexdigest()
                with patch.object(module, "_connection",
                                  side_effect=AssertionError("SQLite accessed")) as connection:
                    with self.assertRaisesRegex(module.ApplyHold, "receipt_noncanonical"):
                        module.apply_once(self.directory, self.job, self.lease, forged)
                    connection.assert_not_called()
        state = handle({"schema": SCHEMA, "operation": "status",
                        "task_id": self.job["task_id"]}, self.directory)
        self.assertEqual(state["state"], "leased")
        self.assertIsNone(state["result_sha256"])

    def test_invalid_digest_does_not_write(self):
        bad = receipt("ok", self.job)
        bad["response_sha256"] = "0" * 64
        with self.assertRaises(module.ApplyHold):
            module.apply_once(self.directory, self.job, self.lease, bad)
        status = handle({"schema": SCHEMA, "operation": "status",
                         "task_id": self.job["task_id"]}, self.directory)
        self.assertEqual(status["state"], "leased")


class BackendIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "engine"
        self.job = dict(root_id="root-test-0001", task_id="task-test-0001",
                        request_id="request-test-0001", work_key="work-test-0001",
                        parent_id="parent-test-0001",
                        conversation_key="conversation-test-0001", executor="hermes",
                        request_sha256=bridge._sha("local test only"))
        self.token = "test-only-original-lease-token"
        self.proof = dict(status="VERIFIED", independent_readback=True, tools=0,
                          task_id=self.job["task_id"], work_key=self.job["work_key"],
                          text="bounded local fixture", response_sha256=bridge._sha("bounded local fixture"))
        self.proof.update({key: self.job[key] for key in ("root_id", "request_id", "parent_id")})
        self.proof["receipt_sha256"] = bridge._sha(module.canonical(self.proof))

    def apply(self):
        return module.apply_once(self.root, self.job, self.token, self.proof)

    def seed_existing_lease(self):
        # Real isolated SQLite schema/row, independent of the backend router.
        import time
        now = int(time.time())
        with closing(bridge._connection(self.root)) as db:
            db.execute("""INSERT INTO jobs
                (id,conversation,parent_id,depth,kind,goal,request_sha,state,
                 lease_sha,lease_until,created_at,updated_at,executor,work_key)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (self.job["task_id"], "conversation-test-0001", self.job["parent_id"],
                 2, "review", "local test only", bridge._sha("local test only"),
                 "leased", bridge._sha(self.token), now + 900, now, now,
                 "hermes", self.job["work_key"]))

    def test_sqlite_backend_missing_storage_never_creates_database(self):
        # A typo/wrong working directory must not cause the reporting adapter
        # to initialize a second empty SQLite mailbox.
        for backend in ("sqlite", None):
            with self.subTest(backend=backend):
                with mock.patch.dict(os.environ):
                    if backend is None:
                        os.environ.pop("MSW_HERMES_CHAT_BACKEND", None)
                    else:
                        os.environ["MSW_HERMES_CHAT_BACKEND"] = backend
                    with mock.patch.object(module, "_connection",
                                           side_effect=AssertionError("SQLite opened")) as connection:
                        with self.assertRaisesRegex(module.ApplyHold, "existing_sqlite_storage_required"):
                            self.apply()
                        connection.assert_not_called()
                    self.assertFalse(self.root.exists(), "adapter bootstrapped a new queue")

    def test_sqlite_backend_refuses_symlinked_storage_before_connection(self):
        # Even a populated-looking symlink is not an admitted local backend.
        alternate = Path(self.tmp.name) / "other"
        alternate.mkdir()
        storage = alternate / "exchange.sqlite3"
        storage.write_bytes(b"not a SQLite database")
        self.root.mkdir()
        (self.root / "hermes-chat-loop").symlink_to(alternate, target_is_directory=True)
        with (mock.patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
              mock.patch.object(module, "_connection",
                                side_effect=AssertionError("SQLite opened")) as connection):
            with self.assertRaisesRegex(module.ApplyHold, "existing_sqlite_storage_required"):
                self.apply()
            connection.assert_not_called()
        self.assertEqual(storage.read_bytes(), b"not a SQLite database")

    def test_sqlite_backend_refuses_symlinked_storage_ancestor_before_connection(self):
        # An alias for the directory (or one of its parents) must not bypass
        # the existing-storage check and open a different mailbox. The file
        # deliberately exists to prove that a basic is_file() check is unsafe.
        self.root.mkdir()
        storage_root = self.root / "hermes-chat-loop"
        storage_root.mkdir()
        database = storage_root / "exchange.sqlite3"
        database.write_bytes(b"sentinel-not-a-real-database")
        root_alias = Path(self.tmp.name) / "engine-alias"
        root_alias.symlink_to(self.root, target_is_directory=True)
        parent_alias = Path(self.tmp.name) / "parent-alias"
        parent_alias.symlink_to(Path(self.tmp.name), target_is_directory=True)
        for alternate in (root_alias, parent_alias / "engine"):
            with self.subTest(alias=str(alternate)), \
                    mock.patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}), \
                    mock.patch.object(module, "_connection",
                                      side_effect=AssertionError("SQLite opened")) as connection, \
                    mock.patch.object(bridge, "handle",
                                      side_effect=AssertionError("report submitted")) as routed:
                with self.assertRaisesRegex(module.ApplyHold,
                                            "existing_sqlite_storage_required"):
                    module.apply_once(alternate, self.job, self.token, self.proof)
                connection.assert_not_called()
                routed.assert_not_called()
        self.assertEqual(database.read_bytes(), b"sentinel-not-a-real-database")

    def test_sqlite_backend_refuses_hardlinked_storage_before_connection(self):
        # A second hard link aliases the same inode without any symlink.
        # The preflight must refuse it before opening or reporting to SQLite.
        other = Path(self.tmp.name) / "original-exchange.sqlite3"
        other.write_bytes(b"hardlink-sentinel-database-bytes")
        storage_root = self.root / "hermes-chat-loop"
        storage_root.mkdir(parents=True)
        database = storage_root / "exchange.sqlite3"
        os.link(other, database)
        self.assertEqual(database.stat().st_nlink, 2)
        with mock.patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}), \
                mock.patch.object(module, "_connection",
                                  side_effect=AssertionError("SQLite opened")) as connection, \
                mock.patch.object(bridge, "handle",
                                  side_effect=AssertionError("report attempted")) as routed:
            with self.assertRaisesRegex(module.ApplyHold,
                                        "existing_sqlite_storage_required"):
                self.apply()
            connection.assert_not_called()
            routed.assert_not_called()
        self.assertEqual(other.read_bytes(), b"hardlink-sentinel-database-bytes")
        self.assertEqual(database.read_bytes(), other.read_bytes())

    def test_sqlite_backend_requires_private_owned_storage_before_connection(self):
        # The bridge's _connection chmods and may migrate the selected file.
        # A different actor's world-readable/writable mailbox, or its parent,
        # must never be opened or silently "fixed" by that routine.
        storage_root = self.root / "hermes-chat-loop"
        storage_root.mkdir(parents=True)
        database = storage_root / "exchange.sqlite3"
        database.write_bytes(b"private-storage-sentinel")
        for target, mode in ((database, 0o644), (database, 0o666),
                             (storage_root, 0o755)):
            with self.subTest(target=target.name, mode=oct(mode)):
                storage_root.chmod(0o700)
                database.chmod(0o600)
                target.chmod(mode)
                with mock.patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}), \
                        mock.patch.object(module, "_connection",
                                          side_effect=AssertionError("SQLite opened")) as connection, \
                        mock.patch.object(bridge, "handle",
                                          side_effect=AssertionError("report attempted")) as routed:
                    with self.assertRaisesRegex(module.ApplyHold,
                                                "existing_sqlite_storage_required"):
                        self.apply()
                    connection.assert_not_called()
                    routed.assert_not_called()
                self.assertEqual(database.read_bytes(), b"private-storage-sentinel")
                self.assertEqual(target.stat().st_mode & 0o777, mode)
        storage_root.chmod(0o700)
        database.chmod(0o600)

    def test_github_backend_rejected_without_creating_sqlite(self):
        with mock.patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "github"}):
            try:
                self.apply()
            except module.ApplyHold as error:
                code = str(error)
            else:
                self.fail("non-SQLite backend was accepted")
        self.assertFalse(self.root.exists(), "rejected GitHub call created local storage")
        self.assertEqual(code, "sqlite_backend_required")

    def test_github_backend_cannot_route_report_from_existing_local_lease(self):
        self.seed_existing_lease()
        with mock.patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "github"}), \
                mock.patch.object(module, "_connection", wraps=module._connection) as connection, \
                mock.patch.object(bridge, "handle", side_effect=AssertionError("remote operation attempted")) as handle:
            with self.assertRaisesRegex(module.ApplyHold, "sqlite_backend_required"):
                self.apply()
            connection.assert_not_called()
            handle.assert_not_called()
        with closing(bridge._connection(self.root)) as db:
            row = db.execute("SELECT state,result,result_sha FROM jobs WHERE id=?", (self.job["task_id"],)).fetchone()
            self.assertEqual(tuple(row), ("leased", None, None))

    def test_unknown_backends_rejected_before_storage(self):
        for backend in ("", "unknown", "GITHUB", "sqlite "):
            with self.subTest(backend=backend), \
                    mock.patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": backend}), \
                    mock.patch.object(module, "_connection", side_effect=AssertionError("storage touched")) as connection:
                with self.assertRaisesRegex(module.ApplyHold, "sqlite_backend_required"):
                    self.apply()
                connection.assert_not_called()

    def test_explicit_sqlite_keeps_real_report_without_native_ack(self):
        self.seed_existing_lease()
        with mock.patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}):
            first, second = self.apply(), self.apply()
        self.assertEqual(first["status"], "STORAGE_REPORT_VERIFIED_NATIVE_UNVERIFIED")
        self.assertTrue(first["storage_readback_verified"])
        self.assertFalse(first["native_hermes_ack_verified"])
        self.assertEqual(first["result_sha256"], second["result_sha256"])
        with closing(bridge._connection(self.root)) as db:
            rows = db.execute("SELECT state,result,result_sha FROM jobs WHERE work_key=?", (self.job["work_key"],)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(tuple(rows[0]), ("reported", self.proof["text"], self.proof["response_sha256"]))

    def test_unset_backend_preserves_existing_sqlite_default(self):
        self.seed_existing_lease()
        with mock.patch.dict(os.environ):
            os.environ.pop("MSW_HERMES_CHAT_BACKEND", None)
            self.assertEqual(self.apply()["status"], "STORAGE_REPORT_VERIFIED_NATIVE_UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
