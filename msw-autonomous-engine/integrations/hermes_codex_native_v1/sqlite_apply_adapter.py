"""Adapter for one existing Hermes SQLite job; no dispatch or automatic retries.

The external executor must supply a verified Codex receipt and the original lease
token. The adapter never creates a task, changes executor or reclaims expired work.
"""
from __future__ import annotations

import hashlib
import json
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
    fields = ("root_id", "task_id", "request_id", "work_key", "parent_id")
    if not isinstance(job, dict) or any(not isinstance(job.get(k), str) or not job[k] for k in fields):
        raise ApplyHold("invalid_identity")
    if not isinstance(lease_token, str) or not 20 <= len(lease_token) <= 256:
        raise ApplyHold("invalid_lease")
    if not isinstance(codex_receipt, dict) or codex_receipt.get("status") != "VERIFIED":
        raise ApplyHold("receipt_unverified")
    if codex_receipt.get("independent_readback") is not True or codex_receipt.get("tools") != 0:
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
        row = db.execute("SELECT id,work_key,parent_id,state FROM jobs WHERE id=?",
                         (job["task_id"],)).fetchone()
        if (row is None or row["work_key"] != job["work_key"]
                or row["parent_id"] != job["parent_id"]):
            raise ApplyHold("work_identity_mismatch")
    report = handle(dict(schema=SCHEMA, operation="report", task_id=job["task_id"],
                         lease_token=lease_token, result=result,
                         result_sha256=digest, outcome="completed"), directory)
    if report.get("state") not in ("reported", "acknowledged") or report.get("result_sha256") != digest:
        raise ApplyHold("report_not_verified")
    # The native ACK verifies the stored bytes and commits acknowledgement.
    ack = handle(dict(schema=SCHEMA, operation="ack", task_id=job["task_id"],
                      result_sha256=digest), directory)
    if ack.get("state") != "acknowledged" or ack.get("hermes_ack_verified") is not True:
        raise ApplyHold("ack_not_verified")
    with closing(_connection(directory)) as db:
        stored = db.execute("SELECT id,work_key,parent_id,state,result,result_sha,outcome FROM jobs WHERE id=?",
                            (job["task_id"],)).fetchone()
        if (stored is None or stored["work_key"] != job["work_key"]
                or stored["parent_id"] != job["parent_id"]
                or stored["state"] != "acknowledged" or stored["result"] != result
                or stored["result_sha"] != digest or stored["outcome"] != "completed"):
            raise ApplyHold("readback_mismatch")
    return {"status": "APPLIED_VERIFIED", "task_id":job["task_id"],
            "work_key":job["work_key"], "result_sha256":digest,
            "native_ack":ack, "readback_verified":True}
