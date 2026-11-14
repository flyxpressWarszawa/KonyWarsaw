"""Read-only Render staging probe for the private GitHub Hermes queue.

This process never claims, reports, acknowledges, or creates work. It reads the
fixed queue and, when referenced by last_ack, its bounded receipt. No secrets or
private task contents are logged. Storage checks are not live-chat proof.
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
    while not stopping:
        if not os.environ.get("MSW_HERMES_GITHUB_TOKEN", "").strip():
            print(json.dumps({"staging_probe": {
                "state": "credential_missing", "receipt_audit_version": RECEIPT_AUDIT_VERSION,
            }}), flush=True)
        else:
            try:
                print(json.dumps({"staging_probe": verified_snapshot(queue)}, separators=(",", ":")), flush=True)
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
