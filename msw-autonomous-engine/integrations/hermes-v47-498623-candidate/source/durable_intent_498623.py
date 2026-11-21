"""Durable, one-shot owner-side intent journal for existing blocked step 498623.

This is NOT an additional task queue. The existing V46 business owner lock must
be held by the caller for every method. The trusted owner supplies an admitted
supplement after authentic scheduled delivery, evidence and grant verification.
No model output, GitHub content or bool is an owner credential.

The journal commits an intent BEFORE a caller may dispatch an ACK/native effect.
After an interrupted or unknown dispatch there is NO retry token: the next
operation is an independent readback of the *original* effect. This module never
calls transports or the native model itself.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat

import profile_498623 as p

SCHEMA = "msw.498623.owner-intent.v1"
PHASES = ("report_admitted", "ack_uncertain", "ack_observed",
          "native_uncertain", "native_observed")


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise p.AdmissionError("invalid_owner_document") from exc


def _pin(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class DurableIntent:
    """Single case under an owner-controlled private path, not a new queue."""

    def __init__(self, path):
        self.path = Path(path)
        p.require(self.path.is_absolute() and self.path.parent.is_dir()
                  and self.path.parent.resolve(strict=True) == self.path.parent
                  and not self.path.is_symlink(), "noncanonical_ledger_path")
        parent = self.path.parent.stat()
        p.require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.geteuid()
                  and not parent.st_mode & 0o077, "private_parent_required")

    def _identity(self):
        p.require(self.path.is_file() and not self.path.is_symlink()
                  and self.path.resolve(strict=True) == self.path,
                  "ledger_missing_or_alias")
        info = self.path.lstat()
        p.require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                  and info.st_nlink == 1 and not info.st_mode & 0o077,
                  "ledger_identity")
        return info.st_dev, info.st_ino

    @contextmanager
    def _db(self, *, write=False):
        before = self._identity()
        db = sqlite3.connect(self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"),
                             uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            p.require(before == self._identity(), "ledger_identity_changed")
            db.execute("PRAGMA busy_timeout=5000")
            if write:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
            else:
                db.execute("PRAGMA query_only=ON")
            yield db
            p.require(before == self._identity(), "ledger_identity_changed")
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def _row(self, db):
        row = db.execute("SELECT * FROM case_state WHERE singleton=1").fetchone()
        p.require(row is not None, "case_state_missing")
        value = dict(row)
        p.require(value["schema"] == SCHEMA
                  and value["phase"] in PHASES
                  and value["root"] == p.ROOT and value["task"] == p.TASK
                  and value["work_key"] == p.WORK_KEY
                  and value["original_result_sha256"] == p.RESULT_SHA,
                  "case_binding_changed")
        return value

    def prepare(self, admission):
        """One-use, immutable authenticated-owner admission.

        The caller MUST have independently verified its grant and scheduled
        provenance against the existing owner transport and current task.
        A fake admission must NEVER be sent to this method on the host.
        """
        p.require(isinstance(admission, dict)
                  and admission.get("schema") == "msw.498623.supplement.v1"
                  and admission.get("root_job_id") == p.ROOT
                  and admission.get("task_id") == p.TASK
                  and admission.get("work_key") == p.WORK_KEY
                  and admission.get("original_result_sha256") == p.RESULT_SHA
                  and admission.get("phase") == "report_admitted"
                  and isinstance(admission.get("report"), str)
                  and p.sha_text(admission["report"]) == admission.get("report_sha256")
                  and isinstance(admission.get("evidence_sha256"), str)
                  and len(admission["evidence_sha256"]) == 64,
                  "owner_admission_not_verified")
        raw = _canonical(admission)
        p.require(len(raw.encode("utf-8")) <= 20000, "owner_admission_too_large")
        if self.path.exists():
            value = self.read()
            p.require(value["admission_sha256"] == _pin(admission),
                      "existing_admission_conflict")
            return value
        # Exclusive creation is the fence. An interrupted partial initialization
        # is NOT automatically replaced or reset by a second process.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with self._db(write=True) as db:
            db.execute("CREATE TABLE case_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
                       "schema TEXT NOT NULL, phase TEXT NOT NULL, root TEXT NOT NULL,"
                       "task TEXT NOT NULL, work_key TEXT NOT NULL,"
                       "original_result_sha256 TEXT NOT NULL, admission_json TEXT NOT NULL,"
                       "admission_sha256 TEXT NOT NULL, ack_json TEXT, ack_sha256 TEXT,"
                       "native_json TEXT, native_sha256 TEXT, native_request_json TEXT, native_request_sha256 TEXT)")
            db.execute("CREATE TRIGGER immutable_case_delete BEFORE DELETE ON case_state BEGIN SELECT RAISE(ABORT,'immutable_case'); END")
            db.execute("CREATE TRIGGER immutable_case_identity BEFORE UPDATE ON case_state WHEN "
                       "NEW.schema IS NOT OLD.schema OR NEW.root IS NOT OLD.root OR NEW.task IS NOT OLD.task OR "
                       "NEW.work_key IS NOT OLD.work_key OR NEW.original_result_sha256 IS NOT OLD.original_result_sha256 OR "
                       "NEW.admission_json IS NOT OLD.admission_json OR NEW.admission_sha256 IS NOT OLD.admission_sha256 OR "
                       "(OLD.native_request_json IS NOT NULL AND (NEW.native_request_json IS NOT OLD.native_request_json OR NEW.native_request_sha256 IS NOT OLD.native_request_sha256)) OR "
                       "(OLD.ack_json IS NOT NULL AND (NEW.ack_json IS NOT OLD.ack_json OR NEW.ack_sha256 IS NOT OLD.ack_sha256)) OR "
                       "(OLD.native_json IS NOT NULL AND (NEW.native_json IS NOT OLD.native_json OR NEW.native_sha256 IS NOT OLD.native_sha256)) "
                       "BEGIN SELECT RAISE(ABORT,'immutable_case'); END")
            db.execute("CREATE TRIGGER forward_only_phase BEFORE UPDATE OF phase ON case_state WHEN NOT ("
                       "NEW.phase=OLD.phase OR (OLD.phase='report_admitted' AND NEW.phase='ack_uncertain') OR "
                       "(OLD.phase='ack_uncertain' AND NEW.phase='ack_observed') OR "
                       "(OLD.phase='ack_observed' AND NEW.phase='native_uncertain') OR "
                       "(OLD.phase='native_uncertain' AND NEW.phase='native_observed')) "
                       "BEGIN SELECT RAISE(ABORT,'invalid_phase_transition'); END")
            db.execute("INSERT INTO case_state(singleton,schema,phase,root,task,work_key,"
                       "original_result_sha256,admission_json,admission_sha256)"
                       " VALUES(1,?,?,?,?,?,?,?,?)",
                       (SCHEMA, "report_admitted", p.ROOT, p.TASK, p.WORK_KEY,
                        p.RESULT_SHA, raw, _pin(admission)))
        fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return self.read()

    def read(self):
        with self._db() as db:
            row = self._row(db)
        admission = p.strict_json(row["admission_json"])
        p.require(_pin(admission) == row["admission_sha256"]
                  and admission.get("report_sha256") == p.sha_text(admission.get("report", ""))
                  and admission.get("phase") == "report_admitted",
                  "admission_corrupt")
        result = {"schema": SCHEMA, "phase": row["phase"], "root_job_id": p.ROOT,
                  "task_id": p.TASK, "work_key": p.WORK_KEY,
                  "original_result_sha256": p.RESULT_SHA,
                  "admission_sha256": row["admission_sha256"],
                  "report_sha256": admission["report_sha256"]}
        content, pinned = row['native_request_json'], row['native_request_sha256']
        p.require((content is None) == (pinned is None), 'partial_native_request')
        result['native_request'] = p.strict_json(content) if content is not None else None
        if content is not None:
            p.require(_pin(result['native_request']) == pinned, 'native_request_corrupt')
        for field in ("ack", "native"):
            content, digest = row[field + "_json"], row[field + "_sha256"]
            p.require((content is None) == (digest is None), "partial_effect_receipt")
            value = p.strict_json(content) if content is not None else None
            if value is not None:
                p.require(_pin(value) == digest, "effect_receipt_corrupt")
            result[field] = value
            result[field + "_sha256"] = digest
        p.require((result['ack'] is not None) == (row['phase'] in {'ack_observed', 'native_uncertain', 'native_observed'})
                  and (result['native'] is not None) == (row['phase'] == 'native_observed'),
                  'phase_receipt_mismatch')
        return result

    def begin_ack(self):
        """Return True ONLY for the first, durable original ACK intent."""
        with self._db(write=True) as db:
            row = self._row(db)
            if row["phase"] != "report_admitted":
                return False
            db.execute("UPDATE case_state SET phase='ack_uncertain' "
                       "WHERE singleton=1 AND phase='report_admitted'")
        return True

    def observe_ack(self, observed):
        """Observe the real blocked ACK; do not convert it to completed."""
        p.require(isinstance(observed, dict), "ack_observation_required")
        required = {"schema": "msw.agent.chat-observation.v1",
                    "root_job_id": p.ROOT, "task_id": p.TASK,
                    "work_key": p.WORK_KEY, "request_id": p.ROOT + ":chat:2",
                    "conversation_id": p.CONVERSATION,
                    "request_binding_sha256": p.BINDING_SHA,
                    "state": "acknowledged", "outcome": "blocked",
                    "result_sha256": p.RESULT_SHA,
                    "result_integrity_verified": True,
                    "request_binding_verified": True,
                    "storage_ack_committed": True}
        p.require(all(observed.get(k) == v for k, v in required.items())
                  and isinstance(observed.get("result"), str)
                  and p.sha_text(observed["result"]) == p.RESULT_SHA,
                  "authenticated_original_ack_required")
        raw = _canonical(observed)
        with self._db(write=True) as db:
            row = self._row(db)
            if row["ack_json"] is not None:
                p.require(row["ack_json"] == raw, "conflicting_original_ack")
                return self.read()
            p.require(row["phase"] == "ack_uncertain", "ack_intent_required")
            db.execute("UPDATE case_state SET phase='ack_observed', ack_json=?,ack_sha256=?"
                       " WHERE singleton=1 AND phase='ack_uncertain'", (raw, _pin(observed)))
        return self.read()

    def begin_native(self):
        """Unknown native outcomes cannot be dispatched twice after restart."""
        with self._db(write=True) as db:
            row = self._row(db)
            if row["phase"] != "ack_observed":
                return False
            p.require(row["ack_json"] is not None, "ack_readback_required")
            db.execute("UPDATE case_state SET phase='native_uncertain'"
                       " WHERE singleton=1 AND phase='ack_observed'")
        return True

    def observe_native(self, request, receipt, proof):
        """Record only independent, hash-bound native-session readback."""
        p.require(all(isinstance(x, dict) for x in (request, receipt, proof)),
                  "native_observations_required")
        p.require(request.get("root_job_id") == p.ROOT
                  and request.get("task_id") == p.TASK
                  and request.get("schema") == "msw.agent.native-application.request.v1"
                  and type(request.get("step_index")) is int and request.get("step_index") == 2
                  and request.get("request_id") == p.ROOT + ":chat:2"
                  and request.get("idempotency_key") == "msw-native-apply-v3:" + p.TASK
                  and request.get("work_key") == p.WORK_KEY
                  and isinstance(request.get("result"), str)
                  and request.get("result_sha256") == p.sha_text(request["result"])
                  and isinstance(request.get("native_request_sha256"), str)
                  and request["native_request_sha256"] == _pin({k: v for k, v in request.items() if k != "native_request_sha256"}),
                  "native_request_mismatch")
        state = self.read()
        p.require(state['native_request'] is None or state['native_request'] == request,
                  'reserved_native_request_changed')
        p.require(request["result_sha256"] == state["report_sha256"]
                  and receipt.get("schema") == "msw.agent.native-application.receipt.v1"
                  and receipt.get("state") == "completed"
                  and receipt.get("idempotency_key") == request["idempotency_key"]
                  and receipt.get("root_job_id") == p.ROOT
                  and receipt.get("task_id") == p.TASK
                  and receipt.get("result_sha256") == state["report_sha256"]
                  and receipt.get("native_request_sha256") == request.get("native_request_sha256")
                  and proof.get("verified") is True
                  and proof.get("source_kind") == "native_session_readback"
                  and proof.get("verification_scope") == "native_hermes"
                  and proof.get("receipt_sha256") == _pin(receipt)
                  and proof.get("native_request_sha256") == request.get("native_request_sha256"),
                  "independent_native_readback_required")
        value = {"request": request, "receipt": receipt, "proof": proof}
        raw = _canonical(value)
        with self._db(write=True) as db:
            row = self._row(db)
            if row["native_json"] is not None:
                p.require(row["native_json"] == raw, "native_observation_conflict")
                return self.read()
            p.require(row["phase"] == "native_uncertain", "native_intent_required")
            db.execute("UPDATE case_state SET phase='native_observed',native_json=?,native_sha256=?"
                       " WHERE singleton=1 AND phase='native_uncertain'", (raw, _pin(value)))
        return self.read()

    def reserve_native(self, request):
        """Persist exact native request before granting one dispatch."""
        state = self.read()
        p.require(isinstance(request, dict)
                  and request.get('schema') == 'msw.agent.native-application.request.v1'
                  and request.get('root_job_id') == p.ROOT and request.get('task_id') == p.TASK
                  and request.get('work_key') == p.WORK_KEY
                  and request.get('idempotency_key') == 'msw-native-apply-v3:' + p.TASK
                  and type(request.get('step_index')) is int and request['step_index'] == 2
                  and request.get('request_id') == p.ROOT + ':chat:2'
                  and isinstance(request.get('result'), str)
                  and p.sha_text(request['result']) == state['report_sha256']
                  and request.get('result_sha256') == state['report_sha256']
                  and request.get('native_request_sha256') == _pin({k: v for k, v in request.items() if k != 'native_request_sha256'}),
                  'native_request_mismatch')
        raw = _canonical(request)
        with self._db(write=True) as db:
            row = self._row(db)
            if row['phase'] != 'ack_observed':
                p.require(row['native_request_json'] == raw, 'native_reservation_conflict')
                return False
            p.require(row['ack_json'] is not None, 'ack_readback_required')
            db.execute("UPDATE case_state SET phase='native_uncertain',native_request_json=?,native_request_sha256=? "
                       "WHERE singleton=1 AND phase='ack_observed'", (raw, _pin(request)))
        return True
