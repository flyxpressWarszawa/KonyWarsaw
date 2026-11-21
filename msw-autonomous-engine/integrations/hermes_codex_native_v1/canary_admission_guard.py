"""Fail-closed eligibility checks for a future Hermes -> native Codex canary.

This module performs no dispatch, queue mutation, or ACK writing. Its output is
advisory; callers MUST independently verify current leases and native apply.
"""
from __future__ import annotations
import hashlib
import json
import re

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,159}\Z")
ELIGIBLE = "ELIGIBLE_FOR_CANARY_REVIEW"
HOLD = "HOLD"

def assess(job: dict) -> dict:
    """Return a deterministic decision without performing side effects."""
    if not isinstance(job, dict):
        return {"state": HOLD, "reason": "INVALID_JOB"}
    required = ("root_id", "task_id", "request_id", "work_key", "parent_id")
    if any(not isinstance(job.get(k), str) or not ID.fullmatch(job[k]) for k in required):
        return {"state": HOLD, "reason": "MISSING_OR_INVALID_IDENTITY"}
    if job.get("state") != "new" or job.get("lease_owner") is not None:
        return {"state": HOLD, "reason": "NOT_NEW_UNCLAIMED"}
    if job.get("origin") != "owner" or job.get("kind") != "text":
        return {"state": HOLD, "reason": "NOT_OWNER_TEXT"}
    if job.get("side_effect_free") is not True:
        return {"state": HOLD, "reason": "SIDE_EFFECT_RISK"}
    if job.get("native_apply_contract_verified") is not True:
        return {"state": HOLD, "reason": "NATIVE_APPLY_UNVERIFIED"}
    if job.get("bounded_output_contract_verified") is not True:
        return {"state": HOLD, "reason": "OUTPUT_CONTRACT_UNVERIFIED"}
    if job.get("idempotency_reservation_committed") is not True:
        return {"state": HOLD, "reason": "RESERVATION_NOT_COMMITTED"}
    if job.get("prior_dispatch") is not False or job.get("prior_effect") is not False:
        return {"state": HOLD, "reason": "PRIOR_OUTCOME_UNKNOWN"}
    identity = {k: job[k] for k in required}
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return {"state": ELIGIBLE, "identity_sha256": hashlib.sha256(canonical).hexdigest(),
            "reason": "REQUIRES_LIVE_LEASE_RECHECK_AND_NATIVE_ACK"}

def test_assess():
    assert assess({})["state"] == HOLD
    assert assess({"state": "held", "task_id": "msw-agent-v1:business:498623"})["state"] == HOLD
    j = dict(root_id="r", task_id="t", request_id="q", work_key="w", parent_id="p",
             state="new", lease_owner=None, origin="owner", kind="text",
             side_effect_free=True, native_apply_contract_verified=True,
             bounded_output_contract_verified=True, idempotency_reservation_committed=True,
             prior_dispatch=False, prior_effect=False)
    assert assess(j)["state"] == ELIGIBLE
    for key in ("native_apply_contract_verified", "bounded_output_contract_verified",
                "idempotency_reservation_committed", "side_effect_free"):
        altered = dict(j, **{key: False})
        assert assess(altered)["state"] == HOLD
    assert assess(dict(j, prior_dispatch=None))["state"] == HOLD

if __name__ == "__main__":
    test_assess()
