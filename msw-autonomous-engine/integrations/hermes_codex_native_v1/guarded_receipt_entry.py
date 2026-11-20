"""Single-lease Codex receipt application; no automatic claim or second dispatch.

The caller must already hold the ORIGINAL Hermes lease.  This bridge never
retries Codex after a timeout: an uncertain execution is HOLD, not a replay.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

from .sqlite_apply_adapter import ApplyHold, apply_once, canonical

REQUIRED = ("root_id", "task_id", "request_id", "work_key", "parent_id")


def apply_codex_result(directory, job, lease_token, receipt, *, dispatch_enabled=False):
    """Apply an already completed, independently read-back Codex result.

    This function never starts Codex, changes routing, or creates a task.
    Its explicit switch prevents an accidental activation by import.
    """
    if dispatch_enabled is not True:
        raise ApplyHold("codex_production_dispatch_disabled")
    if not isinstance(job, dict) or any(
        not isinstance(job.get(k), str) or not job[k] for k in REQUIRED
    ):
        raise ApplyHold("invalid_job_identity")
    if not isinstance(receipt, dict):
        raise ApplyHold("invalid_receipt")
    if receipt.get("status") != "VERIFIED" or receipt.get("independent_readback") is not True:
        raise ApplyHold("unverified_codex_result")
    if receipt.get("tools") != 0:
        raise ApplyHold("tool_execution_not_approved")
    if receipt.get("task_id") != job["task_id"]:
        raise ApplyHold("receipt_task_mismatch")
    if receipt.get("work_key") != job["work_key"]:
        raise ApplyHold("receipt_work_mismatch")
    # Bind Codex output to the *exact* dispatched request and ancestor. SHA
    # alone only proves byte integrity, not that a receipt belongs to this job.
    for field in ("root_id", "request_id", "parent_id"):
        if receipt.get(field) != job[field]:
            raise ApplyHold("receipt_" + field + "_mismatch")
    return apply_once(Path(directory), job, lease_token, receipt)
