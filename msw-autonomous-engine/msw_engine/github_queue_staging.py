"""Read-only Render staging probe for the private GitHub Hermes queue.

This process never claims, reports, acknowledges, or creates work. It is safe to
run beside production because it reads only the fixed queue status document.
"""
from __future__ import annotations

import json
import os
import signal
import time

from .github_chat_queue import GitHubChatQueue, SCHEMA
from .tasks import TaskError


def safe_snapshot(queue: GitHubChatQueue) -> dict:
    result = queue.handle({"schema": SCHEMA, "operation": "status"})
    counts = result.get("counts", {})
    if not isinstance(counts, dict):
        raise TaskError("staging_status_invalid")
    safe_counts = {
        str(state): count
        for state, count in counts.items()
        if state in {"pending", "claimed", "reported", "acknowledged", "uncertain"}
        and isinstance(count, int) and 0 <= count <= 1
    }
    return {
        "state": "read_only_connected",
        "github_queue_connected": result.get("github_queue_connected") is True,
        "live_external_chat_connected": False,
        "counts": safe_counts,
    }


def main() -> int:
    if not os.environ.get("MSW_HERMES_GITHUB_TOKEN", "").strip():
        print(json.dumps({"staging_probe": {"state": "credential_missing"}}), flush=True)
    else:
        print(json.dumps({"staging_probe": {"state": "credential_present"}}), flush=True)

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
            print(json.dumps({"staging_probe": {"state": "credential_missing"}}), flush=True)
        else:
            try:
                print(json.dumps({"staging_probe": safe_snapshot(queue)}, separators=(",", ":")), flush=True)
            except TaskError as exc:
                # Only emit a bounded internal error code, never request headers or secrets.
                code = str(exc).split(":", 1)[-1][:80]
                print(json.dumps({"staging_probe": {"state": "unavailable", "code": code}}), flush=True)
        deadline = time.monotonic() + interval
        while not stopping and time.monotonic() < deadline:
            time.sleep(min(1, deadline - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
