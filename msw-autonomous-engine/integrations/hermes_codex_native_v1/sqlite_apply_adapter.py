"""Adapter for one existing Hermes SQLite job; no dispatch or automatic retries.

The external executor must supply a verified Codex receipt and the original lease
token. The adapter never creates a task, changes executor or reclaims expired work.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import closing

from msw_engine.hermes_chat_bridge import _connection, _sha, _id


class ApplyHold(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def apply_once(directory, job, lease_token, codex_receipt):
    """Atomically persist a verified text result, then independently read back.

    A successful database commit may precede a transport timeout. In that case
    caller must inspect the original task, never dispatch a second execution.
    """
    # Local row validation and routed report/ACK must use the same backend.
    # Refuse before _connection can create or migrate an unrelated SQLite DB.
    if os.environ.get("MSW_HERMES_CHAT_BACKEND", "sqlite") != "sqlite":
        raise ApplyHold("sqlite_backend_required")
    fields = ("root_id", "task_id", "request_id", "work_key", "parent_id")
    if not isinstance(job, dict) or any(not isinstance(job.get(k), str) or not job[k] for k in fields):
        raise ApplyHold("invalid_identity")
    if not isinstance(lease_token, str) or not 20 <= len(lease_token) <= 256:
        raise ApplyHold("invalid_lease")
    if not isinstance(codex_receipt, dict) or codex_receipt.get("status") != "VERIFIED":
        raise ApplyHold("receipt_unverified")
    # The adapter is also importable directly; enforce the same request binding
    # as its entrypoint so bypassing the wrapper cannot weaken admission.
    for field in ("task_id", "work_key", "root_id", "request_id", "parent_id"):
        if codex_receipt.get(field) != job[field]:
            raise ApplyHold("receipt_" + field + "_mismatch")
    if (codex_receipt.get("independent_readback") is not True
            or type(codex_receipt.get("tools")) is not int
            or codex_receipt["tools"] != 0):
        # bool(False) and float(0.0) compare equal to 0; neither proves no tools.
        raise ApplyHold("receipt_unsafe")
    result = codex_receipt.get("text")
    if not isinstance(result, str) or not result.strip() or len(result.encode("utf-8")) > 16384:
        raise ApplyHold("result_invalid")
    receipt_body = {k: v for k, v in codex_receipt.items() if k != "receipt_sha256"}
    if _sha(canonical(receipt_body)) != codex_receipt.get("receipt_sha256"):
        raise ApplyHold("receipt_digest_mismatch")
    digest = _sha(result)
    if digest != codex_receipt.get("response_sha256"):
        raise ApplyHold("result_digest_mismatch")
    from msw_engine.hermes_chat_bridge import SCHEMA, handle
    # The existing report operation owns the SQLite BEGIN IMMEDIATE transaction,
    # validates the lease, and enforces idempotency.
    with closing(_connection(directory)) as db:
        row = db.execute("SELECT id,work_key,parent_id,state,lease_sha,result,result_sha,outcome "
                         "FROM jobs WHERE id=?",
                         (job["task_id"],)).fetchone()
        if (row is None or row["work_key"] != job["work_key"]
                or row["parent_id"] != job["parent_id"]):
            raise ApplyHold("work_identity_mismatch")
        if row["lease_sha"] != _sha(lease_token):
            raise ApplyHold("lease_not_valid")
        if row["state"] in ("reported", "acknowledged"):
            # A previous report may have committed before its response was lost.
            # Read the stored effect; never submit the uncertain report again.
            if (row["result"] != result or row["result_sha"] != digest
                    or row["outcome"] != "completed"):
                raise ApplyHold("conflicting_recorded_result")
            report_already_recorded = True
        elif row["state"] == "leased":
            report_already_recorded = False
        else:
            raise ApplyHold("work_not_leased")
    if not report_already_recorded:
        report = handle(dict(schema=SCHEMA, operation="report", task_id=job["task_id"],
                             lease_token=lease_token, result=result,
                             result_sha256=digest, outcome="completed"), directory)
        if report.get("state") not in ("reported", "acknowledged") or report.get("result_sha256") != digest:
            raise ApplyHold("report_not_verified")
    # This SQLite chat transport's generic "ack" operation only changes a
    # local storage row. It does NOT prove that the actual Hermes process
    # received, validated, or applied the result. Never issue that ACK for
    # Hermes from the Codex adapter or promote transport storage to native apply.
    # The authorized native owner must reconcile and ACK through its own
    # authenticated contract, independently from this method.
    with closing(_connection(directory)) as db:
        stored = db.execute("SELECT id,work_key,parent_id,state,result,result_sha,outcome FROM jobs WHERE id=?",
                            (job["task_id"],)).fetchone()
        if (stored is None or stored["work_key"] != job["work_key"]
                or stored["parent_id"] != job["parent_id"]
                or stored["state"] not in ("reported", "acknowledged")
                or stored["result"] != result or stored["result_sha"] != digest
                or stored["outcome"] != "completed"):
            raise ApplyHold("readback_mismatch")
        storage_state = stored["state"]
    return {"status": "STORAGE_REPORT_VERIFIED_NATIVE_UNVERIFIED",
            "task_id": job["task_id"], "work_key": job["work_key"],
            "result_sha256": digest, "storage_state": storage_state,
            "storage_readback_verified": True,
            "native_hermes_ack_verified": False,
            "native_application_verified": False}
