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

    def test_acknowledged_transport_cannot_regress_to_reported(self):
        proof = receipt("original result", self.job)
        original_handle = bridge.handle

        def commit_then_claim_ack(payload, directory):
            report = original_handle(payload, directory)
            return dict(report, state="acknowledged")

        with (patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
              patch.object(bridge, "handle",
                           side_effect=commit_then_claim_ack) as routed):
            with self.assertRaisesRegex(module.ApplyHold, "readback_mismatch"):
                module.apply_once(self.directory, self.job, self.lease, proof)
            recovered = module.apply_once(self.directory, self.job, self.lease, proof)
            routed.assert_called_once()
        self.assertFalse(recovered["native_hermes_ack_verified"])
        with closing(bridge._connection(self.directory)) as db:
            row = db.execute("SELECT state,result FROM jobs WHERE id=?",
                             (self.job["task_id"],)).fetchone()
        self.assertEqual(tuple(row), ("reported", proof["text"]))

    def test_acknowledged_row_cannot_regress_during_reconciliation(self):
        proof = receipt("original result", self.job)
        with patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}):
            module.apply_once(self.directory, self.job, self.lease, proof)
            handle({"schema": SCHEMA, "operation": "ack",
                    "task_id": self.job["task_id"],
                    "result_sha256": proof["response_sha256"]}, self.directory)
            real_connection = module._connection
            calls = []

            def connection_then_regress(directory):
                calls.append(1)
                if len(calls) == 2:
                    # Fault injection in isolated test storage only.
                    with closing(bridge._connection(directory)) as db:
                        db.execute("UPDATE jobs SET state='reported' WHERE id=?",
                                   (self.job["task_id"],))
                return real_connection(directory)

            with (patch.object(module, "_connection", side_effect=connection_then_regress),
                  patch.object(bridge, "handle", side_effect=AssertionError("replayed")) as routed):
                with self.assertRaisesRegex(module.ApplyHold, "readback_mismatch"):
                    module.apply_once(self.directory, self.job, self.lease, proof)
                routed.assert_not_called()
        self.assertEqual(len(calls), 2)

    def test_wrong_report_depth_after_commit_holds_without_replay(self):
        proof = receipt("original result", self.job)
        original_handle = bridge.handle

        def commit_then_wrong_depth(payload, directory):
            report = original_handle(payload, directory)
            return dict(report, depth=report["depth"] + 1)

        with (patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
              patch.object(bridge, "handle",
                           side_effect=commit_then_wrong_depth) as routed):
            with self.assertRaisesRegex(module.ApplyHold, "report_unknown_outcome"):
                module.apply_once(self.directory, self.job, self.lease, proof)
            recovered = module.apply_once(self.directory, self.job, self.lease, proof)
            routed.assert_called_once()
        self.assertTrue(recovered["storage_readback_verified"])
        self.assertFalse(recovered["native_hermes_ack_verified"])
        with closing(bridge._connection(self.directory)) as db:
            row = db.execute("SELECT depth,state,result FROM jobs WHERE id=?",
                             (self.job["task_id"],)).fetchone()
        self.assertEqual(tuple(row), (2, "reported", proof["text"]))

    def test_post_report_depth_drift_rejects_readback(self):
        proof = receipt("original result", self.job)
        original_handle = bridge.handle

        def report_then_drift(payload, directory):
            report = original_handle(payload, directory)
            # Simulated concurrent corruption in this isolated test database.
            with closing(bridge._connection(directory)) as db:
                db.execute("UPDATE jobs SET depth=depth+1 WHERE id=?",
                           (self.job["task_id"],))
            return report

        with (patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
              patch.object(bridge, "handle",
                           side_effect=report_then_drift) as routed):
            with self.assertRaisesRegex(module.ApplyHold, "readback_mismatch"):
                module.apply_once(self.directory, self.job, self.lease, proof)
            routed.assert_called_once()
        with closing(bridge._connection(self.directory)) as db:
            row = db.execute("SELECT depth,state,result,result_sha FROM jobs WHERE id=?",
                             (self.job["task_id"],)).fetchone()
        self.assertEqual(tuple(row), (3, "reported", proof["text"],
                                     proof["response_sha256"]))

    def test_foreign_report_receipt_after_commit_holds_without_replay(self):
        proof = receipt("original result", self.job)
        original_handle = bridge.handle

        def commit_then_foreign_receipt(payload, directory):
            report = original_handle(payload, directory)
            # The result digest alone does not identify the original task.
            return dict(report, task_id="task-foreign0001")

        with (patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
              patch.object(bridge, "handle",
                           side_effect=commit_then_foreign_receipt) as routed):
            with self.assertRaisesRegex(module.ApplyHold, "report_unknown_outcome"):
                module.apply_once(self.directory, self.job, self.lease, proof)
            recovered = module.apply_once(self.directory, self.job, self.lease, proof)
            routed.assert_called_once()
        self.assertTrue(recovered["storage_readback_verified"])
        self.assertFalse(recovered["native_hermes_ack_verified"])
        self.assertFalse(recovered["native_application_verified"])
        with closing(bridge._connection(self.directory)) as db:
            row = db.execute("SELECT state,result,result_sha,outcome FROM jobs WHERE id=?",
                             (self.job["task_id"],)).fetchone()
        self.assertEqual(tuple(row), ("reported", proof["text"],
                                     proof["response_sha256"], "completed"))

    def test_post_report_lease_drift_rejects_storage_readback(self):
        # Fault injection in the isolated TemporaryDirectory only. The native
        # report commits once; an altered reservation cannot attest that effect.
        proof = receipt("original result", self.job)
        original_handle = bridge.handle
        foreign_digest = "f" * 64

        def report_then_drift(payload, directory):
            report = original_handle(payload, directory)
            with closing(bridge._connection(directory)) as db:
                db.execute("UPDATE jobs SET lease_sha=? WHERE id=?",
                           (foreign_digest, self.job["task_id"]))
            return report

        with (patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
              patch.object(bridge, "handle", side_effect=report_then_drift) as routed):
            with self.assertRaisesRegex(module.ApplyHold, "readback_mismatch"):
                module.apply_once(self.directory, self.job, self.lease, proof)
            with self.assertRaisesRegex(module.ApplyHold, "lease_not_valid"):
                module.apply_once(self.directory, self.job, self.lease, proof)
            routed.assert_called_once()
        with closing(bridge._connection(self.directory)) as db:
            row = db.execute("SELECT state,lease_sha,result,result_sha FROM jobs WHERE id=?",
                             (self.job["task_id"],)).fetchone()
        self.assertEqual(tuple(row), ("reported", foreign_digest, proof["text"],
                                     proof["response_sha256"]))

    def test_receipt_dict_subclass_cannot_attest_different_status(self):
        # A caller object can expose VERIFIED via get() while its actual
        # hashed JSON says REJECTED. The direct adapter must not open SQLite.
        class ConflictingReceipt(dict):
            def get(self, key, default=None):
                if key == "status":
                    return "VERIFIED"
                return super().get(key, default)

        payload = receipt("original result", self.job)
        payload["status"] = "REJECTED"
        payload.pop("receipt_sha256")
        payload["receipt_sha256"] = hashlib.sha256(
            module.canonical(payload).encode()).hexdigest()
        conflicting = ConflictingReceipt(payload)
        self.assertEqual(conflicting.get("status"), "VERIFIED")
        self.assertEqual(conflicting["status"], "REJECTED")
        with (patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
              patch.object(module, "_connection",
                           side_effect=AssertionError("SQLite opened")) as opened,
              patch.object(bridge, "handle",
                           side_effect=AssertionError("report attempted")) as routed):
            with self.assertRaisesRegex(module.ApplyHold, "receipt_unverified"):
                module.apply_once(self.directory, self.job, self.lease, conflicting)
            opened.assert_not_called()
            routed.assert_not_called()
        with closing(bridge._connection(self.directory)) as db:
            row = db.execute("SELECT state,result,result_sha FROM jobs WHERE id=?",
                             (self.job["task_id"],)).fetchone()
        self.assertEqual(tuple(row), ("leased", None, None))

    def test_receipt_digest_subclass_cannot_override_comparison(self):
        class ForgedDigest(str):
            def __eq__(self, other):
                return True
            def __ne__(self, other):
                return False

        proof = receipt("original result", self.job)
        proof["receipt_sha256"] = ForgedDigest("0" * 64)
        with (patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
              patch.object(module, "_connection",
                           side_effect=AssertionError("SQLite opened")) as opened,
              patch.object(bridge, "handle",
                           side_effect=AssertionError("report attempted")) as routed):
            with self.assertRaisesRegex(module.ApplyHold, "receipt_digest_mismatch"):
                module.apply_once(self.directory, self.job, self.lease, proof)
            opened.assert_not_called()
            routed.assert_not_called()
        with closing(bridge._connection(self.directory)) as db:
            row = db.execute("SELECT state,result,result_sha FROM jobs WHERE id=?",
                             (self.job["task_id"],)).fetchone()
        self.assertEqual(tuple(row), ("leased", None, None))

    def test_receipt_nested_status_accessor_never_runs_before_sqlite(self):
        calls = []

        class UntrustedStatus(str):
            def __ne__(self, other):
                calls.append("status comparison")
                return False

        proof = receipt("original result", self.job)
        proof["status"] = UntrustedStatus("REJECTED")
        with (patch.object(module, "_connection",
                           side_effect=AssertionError("SQLite opened")) as opened,
              patch.object(bridge, "handle",
                           side_effect=AssertionError("report attempted")) as routed):
            with self.assertRaisesRegex(module.ApplyHold, "receipt_noncanonical"):
                module.apply_once(self.directory, self.job, self.lease, proof)
            opened.assert_not_called()
            routed.assert_not_called()
        self.assertEqual(calls, [])
        with closing(bridge._connection(self.directory)) as db:
            row = db.execute("SELECT state,result,result_sha FROM jobs WHERE id=?",
                             (self.job["task_id"],)).fetchone()
        self.assertEqual(tuple(row), ("leased", None, None))

    def test_report_mapping_accessor_never_runs_after_real_commit(self):
        calls = []
        answer = "persisted before invalid transport response"
        proof = receipt(answer, self.job)

        class UntrustedReport(dict):
            def get(self, *args):
                calls.append("report accessor")
                raise AssertionError("untrusted report accessor invoked")

        def commit_then_return_mapping(payload, directory):
            self.assertEqual(payload["operation"], "report")
            calls.append("report committed")
            return UntrustedReport(handle(payload, directory))

        with patch.object(bridge, "handle", side_effect=commit_then_return_mapping) as routed:
            with self.assertRaisesRegex(module.ApplyHold, "report_unknown_outcome"):
                module.apply_once(self.directory, self.job, self.lease, proof)
            routed.assert_called_once()
        self.assertEqual(calls, ["report committed"])
        with patch.object(bridge, "handle",
                          side_effect=AssertionError("committed report replayed")) as routed:
            recovered = module.apply_once(self.directory, self.job, self.lease, proof)
            routed.assert_not_called()
        self.assertEqual(recovered["storage_state"], "reported")
        self.assertEqual(recovered["result_sha256"], proof["response_sha256"])
        self.assertFalse(recovered["native_hermes_ack_verified"])
        self.assertFalse(recovered["native_application_verified"])

    def test_lease_subclass_cannot_override_encoded_identity(self):
        class AmbiguousLease(str):
            def encode(inner, *args, **kwargs):
                return self.lease.encode(*args, **kwargs)

        with (patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
              patch.object(module, "_connection",
                           side_effect=AssertionError("SQLite opened")) as opened,
              patch.object(bridge, "handle",
                           side_effect=AssertionError("report attempted")) as routed):
            with self.assertRaisesRegex(module.ApplyHold, "invalid_lease"):
                module.apply_once(self.directory, self.job, AmbiguousLease("x" * 32),
                                  receipt("original result", self.job))
            opened.assert_not_called()
            routed.assert_not_called()
        with closing(bridge._connection(self.directory)) as db:
            row = db.execute("SELECT state,result,result_sha FROM jobs WHERE id=?",
                             (self.job["task_id"],)).fetchone()
        self.assertEqual(tuple(row), ("leased", None, None))

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


    def test_foreign_owned_nonwritable_ancestor_holds_before_report(self):
        # A foreign owner can change a 0755 directory's permissions and
        # replace its children. Current nonwritability is not an owner fence.
        from types import SimpleNamespace
        import stat
        database = self.directory / "hermes-chat-loop" / "exchange.sqlite3"
        original = database.read_bytes()
        ancestor = self.directory.parent
        real_lstat = Path.lstat
        foreign_uid = max(1, os.geteuid() + 1)

        def foreign_ancestor(path, *args, **kwargs):
            if path == ancestor:
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=foreign_uid)
            return real_lstat(path, *args, **kwargs)

        with (patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
              patch.object(Path, "lstat", foreign_ancestor),
              patch.object(module, "_connection", side_effect=AssertionError("SQLite opened")) as opened,
              patch.object(bridge, "handle", side_effect=AssertionError("report attempted")) as routed):
            with self.assertRaisesRegex(module.ApplyHold, "existing_sqlite_storage_required"):
                module.apply_once(self.directory, self.job, self.lease, receipt("ok", self.job))
            opened.assert_not_called()
            routed.assert_not_called()
        self.assertEqual(database.read_bytes(), original)
        with closing(bridge._connection(self.directory)) as db:
            row = db.execute("SELECT state,result,result_sha FROM jobs WHERE id=?",
                             (self.job["task_id"],)).fetchone()
        self.assertEqual(tuple(row), ("leased", None, None))

    def test_mailbox_swap_after_row_read_holds_without_report(self):
        self._check_mailbox_swap_at_close(1)

    def test_mailbox_swap_after_final_read_holds_unknown_without_replay(self):
        self._check_mailbox_swap_at_close(2)

    def _check_mailbox_swap_at_close(self, swap_at):
        database = self.directory / "hermes-chat-loop" / "exchange.sqlite3"
        replacement = self.directory / "replacement.sqlite3"
        parked = self.directory / "original.sqlite3"
        with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(replacement)) as target:
            source.backup(target)
        replacement.chmod(0o600)
        real_connection = module._connection
        calls = []

        def connection(directory):
            db = real_connection(directory)
            class CloseThenSwap:
                def execute(inner, *args, **kwargs):
                    return db.execute(*args, **kwargs)
                def close(inner):
                    db.close()
                    calls.append(True)
                    if len(calls) == swap_at:
                        os.replace(database, parked)
                        os.replace(replacement, database)
            return CloseThenSwap()

        answer = "admitted result"
        expected = ("existing_sqlite_storage_required" if swap_at == 1
                    else "readback_unknown_outcome")
        with (patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
              patch.object(module, "_connection", side_effect=connection),
              patch.object(bridge, "handle", wraps=bridge.handle) as routed):
            with self.assertRaisesRegex(module.ApplyHold, expected):
                module.apply_once(self.directory, self.job, self.lease,
                                  receipt(answer, self.job))
            self.assertEqual(routed.call_count, swap_at - 1)
        self.assertEqual(len(calls), swap_at)
        with closing(sqlite3.connect(parked)) as db:
            original = db.execute("SELECT state,result FROM jobs WHERE id=?",
                                  (self.job["task_id"],)).fetchone()
        with closing(sqlite3.connect(database)) as db:
            shadow = db.execute("SELECT state,result FROM jobs WHERE id=?",
                                (self.job["task_id"],)).fetchone()
        self.assertEqual(original, ("leased", None) if swap_at == 1
                         else ("reported", answer))
        self.assertEqual(shadow, ("leased", None))

    def test_mailbox_permission_drift_after_admission_holds_before_report(self):
        self._check_mailbox_permission_drift(1)

    def test_mailbox_permission_drift_after_final_read_holds_without_replay(self):
        self._check_mailbox_permission_drift(2)

    def _check_mailbox_permission_drift(self, insert_at):
        root = self.directory / "hermes-chat-loop"
        database = root / "exchange.sqlite3"
        mode = root.stat().st_mode & 0o777
        real_connection = module._connection
        calls = []
        proof = receipt("admitted result", self.job)

        def connection(directory):
            db = real_connection(directory)

            class CloseThenDrift:
                def execute(inner, *args, **kwargs):
                    return db.execute(*args, **kwargs)

                def close(inner):
                    db.close()
                    calls.append(True)
                    if len(calls) == insert_at:
                        root.chmod(0o755)

            return CloseThenDrift()

        error = ("existing_sqlite_storage_required" if insert_at == 1
                 else "readback_unknown_outcome")
        with (patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
              patch.object(bridge, "handle", wraps=bridge.handle) as routed):
            try:
                with patch.object(module, "_connection", side_effect=connection):
                    with self.assertRaisesRegex(module.ApplyHold, error):
                        module.apply_once(self.directory, self.job, self.lease, proof)
                self.assertEqual(routed.call_count, insert_at - 1)
            finally:
                root.chmod(mode)
            if insert_at == 2:
                recovered = module.apply_once(self.directory, self.job, self.lease, proof)
                self.assertFalse(recovered["native_hermes_ack_verified"])
                routed.assert_called_once()
        self.assertEqual(len(calls), insert_at)
        with closing(sqlite3.connect(database)) as db:
            row = db.execute("SELECT state,result FROM jobs WHERE id=?",
                             (self.job["task_id"],)).fetchone()
        self.assertEqual(row, ("leased", None) if insert_at == 1
                         else ("reported", proof["text"]))

    def test_sidecar_alias_after_admission_read_holds_before_report(self):
        self._check_sidecar_alias_at_close(1)

    def test_sidecar_alias_after_final_read_holds_without_replay(self):
        self._check_sidecar_alias_at_close(2)

    def _check_sidecar_alias_at_close(self, insert_at):
        database = self.directory / "hermes-chat-loop" / "exchange.sqlite3"
        sidecar = Path(str(database) + "-journal")
        victim = self.directory / "unrelated-sidecar-target"
        victim.write_bytes(b"unchanged unrelated bytes")
        victim.chmod(0o600)
        real_connection = module._connection
        calls = []
        proof = receipt("admitted result", self.job)

        def connection(directory):
            db = real_connection(directory)

            class CloseThenAlias:
                def execute(inner, *args, **kwargs):
                    return db.execute(*args, **kwargs)

                def close(inner):
                    db.close()
                    calls.append(True)
                    if len(calls) == insert_at:
                        sidecar.symlink_to(victim)

            return CloseThenAlias()

        error = ("existing_sqlite_storage_required" if insert_at == 1
                 else "readback_unknown_outcome")
        with (patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
              patch.object(bridge, "handle", wraps=bridge.handle) as routed):
            try:
                with patch.object(module, "_connection", side_effect=connection):
                    with self.assertRaisesRegex(module.ApplyHold, error):
                        module.apply_once(self.directory, self.job, self.lease, proof)
                self.assertEqual(routed.call_count, insert_at - 1)
                self.assertTrue(sidecar.is_symlink())
                self.assertEqual(victim.read_bytes(), b"unchanged unrelated bytes")
            finally:
                if sidecar.is_symlink():
                    sidecar.unlink()
            if insert_at == 2:
                recovered = module.apply_once(self.directory, self.job, self.lease, proof)
                self.assertFalse(recovered["native_hermes_ack_verified"])
                routed.assert_called_once()
        self.assertEqual(len(calls), insert_at)
        with closing(sqlite3.connect(database)) as db:
            row = db.execute("SELECT state,result FROM jobs WHERE id=?",
                             (self.job["task_id"],)).fetchone()
        self.assertEqual(row, ("leased", None) if insert_at == 1
                         else ("reported", proof["text"]))

    def test_backend_drift_during_path_conversion_holds_before_report(self):
        # A PathLike callback can change the routing setting after the first
        # admission. The adapter must not report to the other backend.
        class SwitchingBackend:
            def __fspath__(inner):
                os.environ["MSW_HERMES_CHAT_BACKEND"] = "github"
                return os.fspath(self.directory)

        verified = receipt("original backend", self.job)
        with (patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
              patch.object(module, "_connection", side_effect=AssertionError("SQLite opened")) as opened,
              patch.object(bridge, "handle", side_effect=AssertionError("report attempted")) as routed):
            with self.assertRaisesRegex(module.ApplyHold, "sqlite_backend_required"):
                module.apply_once(SwitchingBackend(), self.job, self.lease, verified)
            opened.assert_not_called()
            routed.assert_not_called()
        with closing(bridge._connection(self.directory)) as db:
            row = db.execute("SELECT state,result,result_sha FROM jobs WHERE id=?",
                             (self.job["task_id"],)).fetchone()
        self.assertEqual(tuple(row), ("leased", None, None))

    def test_backend_drift_after_sqlite_read_holds_before_report(self):
        verified = receipt("original backend", self.job)
        real_connection = module._connection

        def changing_connection(directory):
            db = real_connection(directory)
            os.environ["MSW_HERMES_CHAT_BACKEND"] = "github"
            return db

        with (patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
              patch.object(module, "_connection", side_effect=changing_connection),
              patch.object(bridge, "handle", side_effect=AssertionError("report attempted")) as routed):
            with self.assertRaisesRegex(module.ApplyHold, "sqlite_backend_required"):
                module.apply_once(self.directory, self.job, self.lease, verified)
            routed.assert_not_called()
        with closing(bridge._connection(self.directory)) as db:
            row = db.execute("SELECT state,result,result_sha FROM jobs WHERE id=?",
                             (self.job["task_id"],)).fetchone()
        self.assertEqual(tuple(row), ("leased", None, None))

    def test_job_identity_frozen_before_pathlike_callback(self):
        # Caller mapping is not a durable admission record. A path callback
        # can mutate it after the receipt is verified, before the SQLite read.
        original_id = self.job["task_id"]
        original_work_key = self.job["work_key"]
        caller_job = self.job

        class MutatingPath:
            def __fspath__(inner):
                caller_job["task_id"] = "task-foreign-00002"
                caller_job["work_key"] = "work-foreign-00002"
                caller_job["request_sha256"] = "a" * 64
                return os.fspath(self.directory)

        answer = "original admitted identity"
        verified_receipt = receipt(answer, self.job)
        with patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}):
            outcome = module.apply_once(MutatingPath(), caller_job, self.lease,
                                        verified_receipt)
        self.assertEqual(outcome["task_id"], original_id)
        self.assertEqual(outcome["work_key"], original_work_key)
        self.assertFalse(outcome["native_hermes_ack_verified"])
        self.assertNotEqual(caller_job["task_id"], original_id)
        with closing(bridge._connection(self.directory)) as db:
            original = db.execute("SELECT state,result FROM jobs WHERE id=?",
                                  (original_id,)).fetchone()
            foreign = db.execute("SELECT state FROM jobs WHERE id=?",
                                 (caller_job["task_id"],)).fetchone()
        self.assertEqual(tuple(original), ("reported", answer))
        self.assertIsNone(foreign)

    def test_pathlike_storage_pin_prevents_cross_mailbox_report(self):
        # The preflight normalizes a caller PathLike just once. A hostile or
        # buggy __fspath__ may otherwise return a different existing mailbox
        # on the later SQLite read, report and readback calls. Both mailboxes
        # have an identical leased task: only the admitted original may change.
        original_db = self.directory / "hermes-chat-loop" / "exchange.sqlite3"
        other_root = self.directory / "shadow-mailbox"
        other_dir = other_root / "hermes-chat-loop"
        other_dir.mkdir(parents=True, mode=0o700)
        other_db = other_dir / "exchange.sqlite3"
        with closing(sqlite3.connect(original_db)) as source, closing(sqlite3.connect(other_db)) as target:
            source.backup(target)
        other_db.chmod(0o600)

        class SwitchingPath:
            def __init__(self, first, later):
                self.first, self.later, self.calls = first, later, 0

            def __fspath__(self):
                self.calls += 1
                return os.fspath(self.first if self.calls == 1 else self.later)

        supplied = SwitchingPath(self.directory, other_root)
        answer = "pinned original mailbox"
        with patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}):
            outcome = module.apply_once(supplied, self.job, self.lease,
                                        receipt(answer, self.job))
        self.assertEqual(supplied.calls, 1, "untrusted PathLike resolved again after preflight")
        self.assertEqual(outcome["status"], "STORAGE_REPORT_VERIFIED_NATIVE_UNVERIFIED")
        self.assertFalse(outcome["native_hermes_ack_verified"])
        with closing(sqlite3.connect(original_db)) as db:
            original = db.execute("SELECT state,result FROM jobs WHERE id=?",
                                  (self.job["task_id"],)).fetchone()
        with closing(sqlite3.connect(other_db)) as db:
            shadow = db.execute("SELECT state,result FROM jobs WHERE id=?",
                                (self.job["task_id"],)).fetchone()
        self.assertEqual(original, ("reported", answer))
        self.assertEqual(shadow, ("leased", None))

    def test_sqlite_initial_read_failure_is_stable_hold_without_report(self):
        # A plausible SQLite header does not prove all pages can be read.
        # A raw DatabaseError before report must not invite another execution.
        with patch.object(module, "_connection", side_effect=sqlite3.DatabaseError("damaged page")):
            with patch.object(bridge, "handle", side_effect=AssertionError("report attempted")) as routed:
                with self.assertRaisesRegex(module.ApplyHold, "sqlite_row_read_unknown"):
                    module.apply_once(self.directory, self.job, self.lease,
                                      receipt("unchanged answer", self.job))
                routed.assert_not_called()
        state = handle({"schema": SCHEMA, "operation": "status",
                        "task_id": self.job["task_id"]}, self.directory)
        self.assertEqual(state["state"], "leased")
        self.assertIsNone(state["result_sha256"])

    def test_sqlite_post_report_readback_failure_preserves_unknown_result(self):
        # The real report commits, then the adapter loses SQLite readback.
        # This is an UNKNOWN outcome, not a reason to report or execute again.
        original_connection = module._connection
        calls = 0

        def fail_second_open(directory):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise sqlite3.DatabaseError("readback unavailable")
            return original_connection(directory)

        answer = "committed before readback fault"
        digest = hashlib.sha256(answer.encode()).hexdigest()
        with patch.object(module, "_connection", side_effect=fail_second_open):
            with patch.object(bridge, "handle", wraps=handle) as routed:
                with self.assertRaisesRegex(module.ApplyHold, "readback_unknown_outcome"):
                    module.apply_once(self.directory, self.job, self.lease,
                                      receipt(answer, self.job))
                reports = [call for call in routed.call_args_list
                           if call.args[0]["operation"] == "report"]
                self.assertEqual(len(reports), 1)
        self.assertEqual(calls, 2)
        state = handle({"schema": SCHEMA, "operation": "status",
                        "task_id": self.job["task_id"]}, self.directory)
        self.assertEqual(state["state"], "reported")
        self.assertEqual(state["result_sha256"], digest)
        # No native ACK or second SQLite report was issued by this adapter.

    def test_rejected_origin_readonly_preflight_does_not_migrate_native_schema(self):
        # The native bridge's _connection() can CREATE missing indexes even
        # when the subsequent origin check rejects this task. Admission and
        # independent readback must be read-only until an authorized report.
        mailbox = self.directory / "hermes-chat-loop" / "exchange.sqlite3"
        with closing(bridge._connection(self.directory)) as db:
            db.execute("DROP INDEX ix_jobs_state")
        changed = dict(self.job, conversation_key="foreign-conversation-0001")
        with patch.object(bridge, "handle", side_effect=AssertionError("report attempted")) as routed:
            with self.assertRaisesRegex(module.ApplyHold, "persisted_origin_mismatch"):
                module.apply_once(self.directory, changed, self.lease,
                                  receipt("read-only inspection", changed))
            routed.assert_not_called()
        # Inspect without bridge._connection(), which would recreate the index.
        with sqlite3.connect(mailbox) as db:
            index = db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_jobs_state'"
            ).fetchone()
            row = db.execute(
                "SELECT state,result,result_sha FROM jobs WHERE id=?",
                (self.job["task_id"],)
            ).fetchone()
        self.assertIsNone(index, "read-only admission silently recreated an index")
        self.assertEqual(row, ("leased", None, None))

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

    def test_reported_blocked_is_never_promoted_or_replayed(self):
        # A genuine reported/blocked result is terminal for this task, even
        # if a later caller supplies a digest-valid Codex 'completed' receipt.
        # The original blocked ledger must stay intact; no second report or
        # transport/native ACK may be synthesized from a new receipt.
        blocked = "browser policy blocked the required native test"
        blocked_digest = hashlib.sha256(blocked.encode()).hexdigest()
        handle({"schema": SCHEMA, "operation": "report", "task_id": self.job["task_id"],
                "lease_token": self.lease, "result": blocked,
                "result_sha256": blocked_digest, "outcome": "blocked"}, self.directory)
        with patch("msw_engine.hermes_chat_bridge.handle",
                   side_effect=AssertionError("blocked task report replayed")) as routed:
            with self.assertRaisesRegex(module.ApplyHold, "conflicting_recorded_result"):
                module.apply_once(self.directory, self.job, self.lease,
                                  receipt("fabricated completion", self.job))
            routed.assert_not_called()
        with closing(bridge._connection(self.directory)) as db:
            row = db.execute(
                "SELECT state,outcome,result_sha,result FROM jobs WHERE id=?",
                (self.job["task_id"],)).fetchone()
        self.assertEqual(tuple(row), ("reported", "blocked", blocked_digest, blocked))

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

    def test_sqlite_existing_mailbox_rejects_relative_and_parent_traversal(self):
        # Both spellings resolve to an existing leased mailbox today, but can
        # later resolve elsewhere if cwd or an ancestor is changed. The native
        # adapter must never report through such an alias.
        self.seed_existing_lease()
        database = self.root / "hermes-chat-loop" / "exchange.sqlite3"
        original_bytes = database.read_bytes()
        (self.root.parent / "alias-parent").mkdir()
        aliases = [
            Path(os.path.relpath(self.root, start=Path.cwd())),
            self.root.parent / "alias-parent" / ".." / self.root.name,
        ]
        for alias in aliases:
            with self.subTest(alias=str(alias)):
                self.assertTrue((alias / "hermes-chat-loop" / "exchange.sqlite3").exists())
                with (mock.patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
                      mock.patch.object(module, "_connection",
                                        side_effect=AssertionError("SQLite opened")) as connection,
                      mock.patch.object(bridge, "handle",
                                        side_effect=AssertionError("report attempted")) as routed):
                    with self.assertRaisesRegex(module.ApplyHold,
                                                "existing_sqlite_storage_required"):
                        module.apply_once(alias, self.job, self.token, self.proof)
                    connection.assert_not_called()
                    routed.assert_not_called()
                self.assertEqual(database.read_bytes(), original_bytes)

    def test_malformed_filesystem_path_holds_without_open_or_report(self):
        # An embedded NUL or an unencodable Unicode scalar can raise from
        # pathlib.lstat(). These are stable admission denials, never a reason
        # for the caller to retry or select a different storage backend.
        self.seed_existing_lease()
        original_db = self.root / "hermes-chat-loop" / "exchange.sqlite3"
        original_bytes = original_db.read_bytes()
        for malformed in (str(self.root) + "\\x00-other",
                          str(self.root) + "\\ud800"):
            malformed = malformed.replace("\\x00", "\x00").replace("\\ud800", "\ud800")
            with self.subTest(kind=repr(malformed[-8:])):
                with (mock.patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
                      mock.patch.object(module, "_connection",
                                        side_effect=AssertionError("SQLite opened")) as connection,
                      mock.patch.object(bridge, "handle",
                                        side_effect=AssertionError("report attempted")) as routed):
                    with self.assertRaisesRegex(module.ApplyHold,
                                                "existing_sqlite_storage_required"):
                        module.apply_once(malformed, self.job, self.token, self.proof)
                    connection.assert_not_called()
                    routed.assert_not_called()
                self.assertEqual(original_db.read_bytes(), original_bytes)
        with closing(bridge._connection(self.root)) as db:
            row = db.execute("SELECT state,result,result_sha FROM jobs WHERE id=?",
                             (self.job["task_id"],)).fetchone()
        self.assertEqual(tuple(row), ("leased", None, None))

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

    def test_untrusted_writable_ancestor_refused_before_report(self):
        # The selected mailbox itself is private, but an untrusted ancestor
        # without sticky protection could be renamed between checks and open.
        unsafe_parent = Path(self.tmp.name) / "untrusted-parent"
        unsafe_parent.mkdir(mode=0o700)
        self.root = unsafe_parent / "engine"
        self.seed_existing_lease()
        mailbox = self.root / "hermes-chat-loop" / "exchange.sqlite3"
        self.root.chmod(0o700)
        mailbox.parent.chmod(0o700)
        mailbox.chmod(0o600)
        original_bytes = mailbox.read_bytes()
        for mode in (0o770, 0o777):
            with self.subTest(ancestor_mode=oct(mode)):
                unsafe_parent.chmod(mode)
                with (mock.patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
                      mock.patch.object(module, "_connection",
                                        side_effect=AssertionError("SQLite opened")) as connection,
                      mock.patch.object(bridge, "handle",
                                        side_effect=AssertionError("report attempted")) as routed):
                    with self.assertRaisesRegex(module.ApplyHold,
                                                "existing_sqlite_storage_required"):
                        self.apply()
                    connection.assert_not_called()
                    routed.assert_not_called()
                self.assertEqual(mailbox.read_bytes(), original_bytes)
        unsafe_parent.chmod(0o700)

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

    def test_empty_or_malformed_private_mailbox_holds_before_sqlite_open(self):
        # An existing private pathname is not evidence of the original DB.
        # Refuse zero-length and non-SQLite bytes without opening or migrating.
        self.root.mkdir()
        storage = self.root / "hermes-chat-loop"
        storage.mkdir(mode=0o700)
        database = storage / "exchange.sqlite3"
        for bad_bytes in (b"", b"not-sqlite-original-mailbox"):
            with self.subTest(contents=bad_bytes):
                database.write_bytes(bad_bytes)
                database.chmod(0o600)
                with (mock.patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
                      mock.patch.object(module, "_connection",
                                        side_effect=AssertionError("SQLite opened")) as connection,
                      mock.patch.object(bridge, "handle",
                                        side_effect=AssertionError("report attempted")) as routed):
                    with self.assertRaisesRegex(module.ApplyHold,
                                                "existing_sqlite_storage_required"):
                        self.apply()
                    connection.assert_not_called()
                    routed.assert_not_called()
                self.assertEqual(database.read_bytes(), bad_bytes)

    def test_private_mailbox_inode_substitution_during_open_holds(self):
        # If the path changes between lstat and the guarded header open,
        # fstat must reject the wrong inode. This does not claim a full
        # race-free SQLite/open transaction or native fencing.
        self.seed_existing_lease()
        database = self.root / "hermes-chat-loop" / "exchange.sqlite3"
        original = database.read_bytes()
        alternate = Path(self.tmp.name) / "another-private-mailbox"
        alternate.write_bytes(b"SQLite format 3\x00" + b"unrelated")
        alternate.chmod(0o600)
        actual_open = os.open

        def open_other_inode(path, flags, *args, **kwargs):
            if os.fspath(path) == os.fspath(database):
                self.assertTrue(flags & os.O_NONBLOCK, "raced FIFO could block open")
                self.assertTrue(flags & os.O_NOFOLLOW, "raced symlink could redirect")
                return actual_open(alternate, flags, *args, **kwargs)
            return actual_open(path, flags, *args, **kwargs)

        with (mock.patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
              mock.patch.object(module.os, "open", side_effect=open_other_inode),
              mock.patch.object(module, "_connection",
                                side_effect=AssertionError("SQLite opened")) as connection,
              mock.patch.object(bridge, "handle",
                                side_effect=AssertionError("report attempted")) as routed):
            with self.assertRaisesRegex(module.ApplyHold,
                                        "existing_sqlite_storage_required"):
                self.apply()
            connection.assert_not_called()
            routed.assert_not_called()
        self.assertEqual(database.read_bytes(), original)
        self.assertEqual(alternate.read_bytes(), b"SQLite format 3\x00" + b"unrelated")

    def test_private_mailbox_path_swapped_after_header_read_holds(self):
        # fstat on the original open fd cannot see an os.replace at its
        # pathname. SQLite would open a different inode after the header read.
        # The extra named-inode check must HOLD without calling _connection.
        self.seed_existing_lease()
        database = self.root / "hermes-chat-loop" / "exchange.sqlite3"
        original_bytes = database.read_bytes()
        parked = Path(self.tmp.name) / "original-mailbox"
        replacement = Path(self.tmp.name) / "replacement-mailbox"
        replacement_bytes = b"SQLite format 3\x00" + b"foreign-private-mailbox"
        replacement.write_bytes(replacement_bytes)
        replacement.chmod(0o600)
        actual_read = os.read
        swapped = []

        def read_then_swap_path(fd, size):
            header = actual_read(fd, size)
            if size == 16:
                os.replace(database, parked)
                os.replace(replacement, database)
                swapped.append(True)
            return header

        with (mock.patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
              mock.patch.object(module.os, "read", side_effect=read_then_swap_path),
              mock.patch.object(module, "_connection",
                                side_effect=AssertionError("SQLite opened")) as connection,
              mock.patch.object(bridge, "handle",
                                side_effect=AssertionError("report attempted")) as routed):
            with self.assertRaisesRegex(module.ApplyHold,
                                        "existing_sqlite_storage_required"):
                self.apply()
            connection.assert_not_called()
            routed.assert_not_called()
        self.assertEqual(swapped, [True])
        self.assertEqual(parked.read_bytes(), original_bytes)
        self.assertEqual(database.read_bytes(), replacement_bytes)

    def test_private_mailbox_mode_change_during_header_read_holds(self):
        # The same inode may become unsafe after the first fstat but before
        # the header read returns. An old descriptor snapshot must not admit
        # a subsequently world-readable SQLite mailbox for reporting.
        self.seed_existing_lease()
        database = self.root / "hermes-chat-loop" / "exchange.sqlite3"
        original_header = database.read_bytes()[:16]
        actual_read = os.read
        calls = []

        def read_then_relax_mode(fd, size):
            header = actual_read(fd, size)
            if size == 16:
                os.fchmod(fd, 0o644)
                calls.append("permissions_changed")
            return header

        with (mock.patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
              mock.patch.object(module.os, "read", side_effect=read_then_relax_mode),
              mock.patch.object(module, "_connection",
                                side_effect=AssertionError("SQLite opened")) as connection,
              mock.patch.object(bridge, "handle",
                                side_effect=AssertionError("report attempted")) as routed):
            with self.assertRaisesRegex(module.ApplyHold,
                                        "existing_sqlite_storage_required"):
                self.apply()
            connection.assert_not_called()
            routed.assert_not_called()
        self.assertEqual(calls, ["permissions_changed"])
        self.assertEqual(database.read_bytes()[:16], original_header)
        self.assertEqual(database.stat().st_mode & 0o777, 0o644)
        database.chmod(0o600)
        with closing(bridge._connection(self.root)) as db:
            row = db.execute("SELECT state,result,result_sha FROM jobs WHERE id=?",
                             (self.job["task_id"],)).fetchone()
        self.assertEqual(tuple(row), ("leased", None, None))

    def test_writable_mailbox_base_holds_before_sqlite_open(self):
        # A private database is not safe inside a caller-selected directory
        # writable by another UID. Opening it can follow a swapped pathname.
        # This tests one admission gate; it is not inode-level race proof.
        self.root.mkdir()
        storage_root = self.root / "hermes-chat-loop"
        storage_root.mkdir(mode=0o700)
        database = storage_root / "exchange.sqlite3"
        original = b"unaltered-private-mailbox"
        database.write_bytes(original)
        database.chmod(0o600)
        for mode in (0o770, 0o707):
            with self.subTest(mode=oct(mode)):
                self.root.chmod(mode)
                with (mock.patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
                      mock.patch.object(module, "_connection",
                                        side_effect=AssertionError("SQLite opened")) as connection,
                      mock.patch.object(bridge, "handle",
                                        side_effect=AssertionError("report called")) as routed):
                    with self.assertRaisesRegex(module.ApplyHold,
                                                "existing_sqlite_storage_required"):
                        self.apply()
                    connection.assert_not_called()
                    routed.assert_not_called()
                self.assertEqual(database.read_bytes(), original)
                self.assertEqual(self.root.stat().st_mode & 0o777, mode)
        self.root.chmod(0o700)

    def test_sqlite_sidecar_alias_or_unsafe_permissions_never_open_storage(self):
        # SQLite opens WAL/SHM/journal sidecars itself. Check them even when
        # the primary DB path is private and unaliased.
        self.root.mkdir()
        storage_root = self.root / "hermes-chat-loop"
        storage_root.mkdir(mode=0o700)
        database = storage_root / "exchange.sqlite3"
        database.write_bytes(b"unchanged-main-database")
        database.chmod(0o600)
        victim = Path(self.tmp.name) / "unrelated-file"
        victim.write_bytes(b"unrelated-private-bytes")
        victim.chmod(0o600)
        for suffix in ("-wal", "-shm", "-journal"):
            for variant in ("symlink", "hardlink", "public"):
                with self.subTest(suffix=suffix, variant=variant):
                    sidecar = Path(str(database) + suffix)
                    if variant == "symlink":
                        sidecar.symlink_to(victim)
                    elif variant == "hardlink":
                        os.link(victim, sidecar)
                    else:
                        sidecar.write_bytes(b"private-sidecar-before-mode-change")
                        sidecar.chmod(0o644)
                    try:
                        with (mock.patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "sqlite"}),
                              mock.patch.object(module, "_connection",
                                                side_effect=AssertionError("SQLite opened")) as connection,
                              mock.patch.object(bridge, "handle",
                                                side_effect=AssertionError("report attempted")) as routed):
                            with self.assertRaisesRegex(module.ApplyHold,
                                                        "existing_sqlite_storage_required"):
                                self.apply()
                            connection.assert_not_called()
                            routed.assert_not_called()
                        self.assertEqual(database.read_bytes(), b"unchanged-main-database")
                        self.assertEqual(victim.read_bytes(), b"unrelated-private-bytes")
                    finally:
                        sidecar.unlink()

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
