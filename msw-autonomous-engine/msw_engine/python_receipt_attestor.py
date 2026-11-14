"""Second-party read-only attestation of already completed Python staging work.

No model calls, no WordPress requests, no GitHub writes, no queue claim, and
crucially no central Hermes ACK. Strictly limited to the two owner-authorized
synthetic tasks from the previously completed pilot.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import json
import os
import re

from .python_task_pilot import (
    GitHubStore, PilotError, REQUEST_PATH, IDS, SERVICE,
    MAX_BYTES, result_path, validate_manifest, execute, sha, wire,
)

MANIFEST_SHA = "0614fff2f9cadf2f819ab8bf426bcd6b2f7658df789553a40978499b56199f72"
ACK_SCHEMA = "msw.hermes.python-receipt.central-ack.v1"
ACK_ACTOR = "hermes-render-central"
ACK_RUNTIME = "Render/srv-dbltdb9srm7s73ec0v00"
ACK_PURPOSE = "staging_receipts_consumed"
RECEIPT_KEYS = {
    "schema", "task_id", "kind", "request_sha256", "manifest_sha256",
    "output", "result_sha256", "executor_service", "git_commit",
    "executed_at_utc", "production_mutation_performed", "provider_calls",
    "central_hermes_ack_verified", "chatgpt_delivery_verified",
}


class AttestationError(PilotError):
    pass


def _parse_strict(raw: bytes):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise AttestationError("receipt_size")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        if not isinstance(value, dict) or wire(value) != raw:
            raise ValueError("noncanonical")
        return value
    except (ValueError, TypeError, UnicodeError):
        raise AttestationError("receipt_invalid_json") from None


def verify_previous_pilot(store, manifest_sha=MANIFEST_SHA, calculator=execute):
    """Independent readback + recomputation; returns no customer or private body."""
    if manifest_sha != MANIFEST_SHA:
        raise AttestationError("unexpected_manifest_digest")
    tasks = validate_manifest(store.get(REQUEST_PATH), manifest_sha)
    if {t["task_id"] for t in tasks} != IDS or len(tasks) != len(IDS):
        raise AttestationError("expected_two_tasks")
    proofs = []
    for task in sorted(tasks, key=lambda row: row["task_id"]):
        ident = task["task_id"]
        raw = store.get(result_path(ident))
        if raw is None:
            raise AttestationError("missing_receipt")
        receipt = _parse_strict(raw)
        if set(receipt) != RECEIPT_KEYS:
            raise AttestationError("receipt_fields")
        if (receipt["schema"] != "msw.python-task-pilot.receipt.v1"
                or receipt["task_id"] != ident or receipt["kind"] != task["kind"]
                or receipt["request_sha256"] != sha(wire(task))
                or receipt["manifest_sha256"] != manifest_sha):
            raise AttestationError("task_binding")
        if (receipt["executor_service"] != SERVICE
                or type(receipt["provider_calls"]) is not int or receipt["provider_calls"] != 0
                or receipt["production_mutation_performed"] is not False
                or receipt["central_hermes_ack_verified"] is not False
                or receipt["chatgpt_delivery_verified"] is not False):
            raise AttestationError("claimed_scope")
        if not isinstance(receipt["git_commit"], str) or not re.fullmatch(r"[a-f0-9]{40}", receipt["git_commit"]):
            raise AttestationError("runtime_commit_invalid")
        try:
            stamp = datetime.fromisoformat(receipt["executed_at_utc"].replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp.astimezone(timezone.utc) > datetime.now(timezone.utc) + timedelta(minutes=5):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise AttestationError("receipt_time_invalid") from None
        output = receipt["output"]
        if not isinstance(output, dict) or receipt["result_sha256"] != sha(wire(output)):
            raise AttestationError("output_integrity")
        # Use the existing deterministic handler, but no job-supplied handler/path.
        if calculator(task, None) != output:
            raise AttestationError("output_recompute_mismatch")
        proofs.append({
            "task_id": ident,
            "kind": task["kind"],
            "receipt_sha256": sha(raw),
            "result_sha256": receipt["result_sha256"],
            "request_sha256": receipt["request_sha256"],
            "verified_against_pinned_manifest": True,
        })
    bundle = {
        "schema": "msw.python-pilot.attested-bundle.v1",
        "manifest_sha256": manifest_sha,
        "proofs": proofs,
    }
    return {
        **bundle,
        "bundle_sha256": sha(wire(bundle)),
        "state": "staging_receipts_independently_verified",
        "actual_python_execution_checked": True,
        "central_hermes_ack_verified": False,
        "chatgpt_delivery_verified": False,
        "production_mutation_performed": False,
        "provider_calls": 0,
    }


def verify_central_ack_signature(document, bundle_sha, secret, *, now=None, consumed_nonces=None):
    """Protocol-only contract. Caller must authenticate actual actor and store nonce.

    A locally valid HMAC alone is NOT proof that the production Hermes process
    emitted it; until central runtime delivery/readback is observed, no live
    Hermes ACK may be asserted.
    """
    expected = {"schema", "agent_id", "runtime", "purpose", "bundle_sha256",
                "issued_at_utc", "nonce", "signature"}
    if not isinstance(document, dict) or set(document) != expected:
        raise AttestationError("ack_schema")
    if (document["schema"] != ACK_SCHEMA or document["agent_id"] != ACK_ACTOR
            or document["runtime"] != ACK_RUNTIME or document["purpose"] != ACK_PURPOSE
            or not isinstance(bundle_sha, str) or not re.fullmatch(r"[a-f0-9]{64}", bundle_sha)
            or document["bundle_sha256"] != bundle_sha):
        raise AttestationError("ack_scope")
    nonce, signature = document["nonce"], document["signature"]
    if (not isinstance(nonce, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", nonce)
            or not isinstance(signature, str) or not re.fullmatch(r"[a-f0-9]{64}", signature)):
        raise AttestationError("ack_identifiers")
    if consumed_nonces is not None and nonce in consumed_nonces:
        raise AttestationError("ack_replay")
    try:
        stamp = datetime.fromisoformat(document["issued_at_utc"].replace("Z", "+00:00"))
        clock = now or datetime.now(timezone.utc)
        if stamp.tzinfo is None or abs((clock - stamp).total_seconds()) > 300:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise AttestationError("ack_expired") from None
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise AttestationError("ack_verifier_not_configured")
    expected_sig = hmac.new(secret, wire({k: v for k, v in document.items() if k != "signature"}),
                            hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_sig):
        raise AttestationError("ack_signature_invalid")
    return {"signature_matched": True, "bundle_sha256": bundle_sha,
            "central_runtime_attested_by_live_transport": False,
            "central_hermes_ack_verified": False}


def run_live_once():
    if os.environ.get("MSW_HERMES_RECEIPT_ATTEST_STAGING") != "1":
        return {"state": "disabled"}
    if (os.environ.get("RENDER_SERVICE_ID") != SERVICE
            or os.environ.get("MSW_HERMES_PYTHON_TASK_PILOT") == "1"
            or os.environ.get("MSW_HERMES_PRODUCTION_MUTATION_ALLOWED") == "1"):
        return {"state": "policy_blocked"}
    try:
        return verify_previous_pilot(GitHubStore())
    except (AttestationError, PilotError) as exc:
        return {"state": "blocked", "code": str(exc)[:80]}
    except Exception:
        return {"state": "uncertain_no_retry"}
