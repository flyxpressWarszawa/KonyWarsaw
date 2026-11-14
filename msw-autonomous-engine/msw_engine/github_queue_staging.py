"""Render staging probe with explicitly opted-in bounded integration pilots.

Default mode only reads the fixed chat queue and its bounded receipts. Optional
pilots have separate scopes; no status or execution receipt proves live chat.
Private task contents and credentials are never logged.
"""
from __future__ import annotations

import json
import os
import signal
import time

from .github_chat_queue import GitHubChatQueue, SCHEMA
from .github_chat_receipt_audit import audit_snapshot, ReceiptAuditError
from .tasks import TaskError

RECEIPT_AUDIT_VERSION = 1


def optional_one_shot(queue: GitHubChatQueue, environment=None, reader=None) -> dict:
    """Never claim anything unless the operator opted into one exact read-only job."""
    env = os.environ if environment is None else environment
    if env.get("MSW_HERMES_STAGING_PYTHON_ONE_SHOT") != "1":
        return {"state": "disabled", "reported": False}
    if (env.get("MSW_HERMES_CHAT_BACKEND") != "github"
            or env.get("MSW_HERMES_CHAT_LOOP_ENABLED") != "1"
            or env.get("MSW_HERMES_PRODUCTION_MUTATION_ALLOWED") == "1"):
        return {"state": "policy_blocked", "reported": False}
    from .github_python_hub_once import run_exact_readonly_task, read_hub_status
    return run_exact_readonly_task(
        queue, task_id=env.get("MSW_HERMES_STAGING_TASK_ID", ""),
        conversation_id=env.get("MSW_HERMES_CONVERSATION_KEY", ""),
        reader=reader if reader is not None else read_hub_status,
    )



def safe_snapshot(queue: GitHubChatQueue) -> dict:
    result = queue.handle({"schema": SCHEMA, "operation": "status"})
    counts = result.get("counts", {})
    if not isinstance(counts, dict):
        raise TaskError("staging_status_invalid")
    safe_counts = {
        str(state): count
        for state, count in counts.items()
        if state in {"pending", "claimed", "reported", "acknowledged", "uncertain"}
        and type(count) is int and 0 <= count <= 1
    }
    return {
        "state": "read_only_connected",
        "github_queue_connected": result.get("github_queue_connected") is True,
        "live_external_chat_connected": False,
        "counts": safe_counts,
    }


def verified_snapshot(queue: GitHubChatQueue) -> dict:
    """Check queue/receipt consistency without trusting a summary's ACK label."""
    summary = safe_snapshot(queue)
    if summary["github_queue_connected"] is not True:
        raise TaskError("staging_not_connected")
    audit = audit_snapshot(queue)
    # Use the independently checked snapshot, not possibly stale summary counts.
    summary["counts"] = audit["counts"]
    summary["receipt_audit_version"] = RECEIPT_AUDIT_VERSION
    summary["receipt_audit"] = audit
    return summary


def main() -> int:
    present = bool(os.environ.get("MSW_HERMES_GITHUB_TOKEN", "").strip())
    print(json.dumps({"staging_probe": {
        "state": "credential_present" if present else "credential_missing",
        "receipt_audit_version": RECEIPT_AUDIT_VERSION,
    }}), flush=True)

    interval = max(60, min(3600, int(os.environ.get("MSW_HERMES_STAGING_POLL_SECONDS", "900"))))
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    queue = GitHubChatQueue()
    one_shot_handled = False
    registry_sync_handled = False
    if os.environ.get("MSW_HERMES_PYTHON_TASK_PILOT") == "1":
        try:
            from .python_task_pilot import run_live_once as run_python_pilot
            print(json.dumps({"python_task_pilot": run_python_pilot()}, separators=(",", ":")), flush=True)
        except Exception:
            print(json.dumps({"python_task_pilot": {"state": "uncertain_no_retry"}}), flush=True)
    while not stopping:
        if not registry_sync_handled and os.environ.get("MSW_HERMES_WP_REGISTRY_STAGING_ENABLED") == "1":
            registry_sync_handled = True
            try:
                from .wp_registry_staging import run_live_once
                result = run_live_once()
                print(json.dumps({"wp_registry_staging": result}, separators=(",", ":")), flush=True)
            except Exception:
                print(json.dumps({"wp_registry_staging": {"state": "uncertain_no_retry"}}), flush=True)
        if not os.environ.get("MSW_HERMES_GITHUB_TOKEN", "").strip():
            print(json.dumps({"staging_probe": {
                "state": "credential_missing", "receipt_audit_version": RECEIPT_AUDIT_VERSION,
            }}), flush=True)
        else:
            try:
                print(json.dumps({"staging_probe": verified_snapshot(queue)}, separators=(",", ":")), flush=True)
                if not one_shot_handled and os.environ.get("MSW_HERMES_STAGING_PYTHON_ONE_SHOT") == "1":
                    try:
                        result = optional_one_shot(queue)
                        safe = {key: value for key, value in result.items()
                                if key in {"state", "reported", "outcome", "result_sha256",
                                           "hermes_ack_verified", "external_chat_verified"}}
                        print(json.dumps({"staging_python_one_shot": safe}, separators=(",", ":")), flush=True)
                        if result.get("state") != "empty":
                            one_shot_handled = True
                    except Exception:
                        # Unknown write outcomes are never retried automatically.
                        one_shot_handled = True
                        print(json.dumps({"staging_python_one_shot": {"state": "uncertain_manual_review"}}), flush=True)
            except (TaskError, ReceiptAuditError) as exc:
                # Internal code only; no request headers, credentials or result text.
                code = str(exc).split(":", 1)[-1][:80]
                print(json.dumps({"staging_probe": {
                    "state": "unavailable", "code": code,
                    "receipt_audit_version": RECEIPT_AUDIT_VERSION,
                }}), flush=True)
        deadline = time.monotonic() + interval
        while not stopping and time.monotonic() < deadline:
            time.sleep(min(1, deadline - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
