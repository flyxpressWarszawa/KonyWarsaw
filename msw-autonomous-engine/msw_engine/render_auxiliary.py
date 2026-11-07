"""Low-rate SEO queue consumer for an independently registered WP node.

Only deterministic seo.analyze jobs are claimed. No scheduler, publication,
AI call, satellite operation, or private knowledge job is started here. The
local journal is ephemeral; an interrupted SEO calculation may be recomputed
when its server-side lease expires. It must not be used for other job kinds.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError

from .client import Client, TransportError
from .runner import Worker, WorkerDraining

CAPABILITY = "seo.analyze"
POLL_SECONDS = 300
MAX_RETRY_SECONDS = 3600
MAX_SERVER_WAIT_SECONDS = 7 * 24 * 60 * 60


class ProtocolError(Exception):
    """An unexpected queue response must stop this consumer's requests."""


def parse_retry_after(value, now=None):
    """Honor delta seconds and HTTP dates without shortening large values.

    If the requested wait is more than seven days, stop automatic requests
    instead of silently retrying sooner. Invalid headers use normal backoff.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > 128:
        raise ProtocolError("retry_after_too_long")
    if value.isascii() and value.isdigit():
        if len(value) > 9:
            raise ProtocolError("retry_after_too_long")
        seconds = int(value)
    else:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = max(0, math.ceil(date.timestamp() - (time.time() if now is None else now)))
        except (TypeError, ValueError, OverflowError, IndexError):
            return None
    if seconds > MAX_SERVER_WAIT_SECONDS:
        raise ProtocolError("retry_after_too_long")
    return seconds


class RetryHeaderCapture:
    """Observe only the server's retry header; never log response bodies."""

    def __init__(self, opener):
        self.opener = opener
        self.retry_after = None

    def open(self, *args, **kwargs):
        try:
            return self.opener.open(*args, **kwargs)
        except HTTPError as error:
            self.retry_after = error.headers.get("Retry-After") if error.headers else None
            raise


class RetryAwareClient(Client):
    """Keep the existing signed transport and preserve the full retry delay."""

    def send(self, action, payload):
        original = self.opener
        capture = RetryHeaderCapture(original)
        self.opener = capture
        try:
            return super().send(action, payload)
        except TransportError as error:
            if error.status in (429, 503, 504):
                server_wait = parse_retry_after(capture.retry_after)
                if server_wait is not None:
                    error.retry_after = max(error.retry_after, server_wait)
            raise
        finally:
            self.opener = original


def emit(state: str, **details) -> None:
    print(json.dumps({"render_auxiliary": {"state": state, "at": int(time.time()), **details}},
                     separators=(",", ":")), flush=True)


def retry_delay(failures: int, retry_after: int = 0) -> int:
    # One failed call causes at least five minutes of silence; repeated
    # failures exponentially increase the delay. A longer valid server wait
    # takes precedence over the locally calculated ceiling.
    return max(retry_after, min(MAX_RETRY_SECONDS, POLL_SECONDS * 2 ** min(max(failures - 1, 0), 4)))


def step(client: Client, worker: Worker, stopping=lambda: False) -> dict:
    if stopping():
        raise WorkerDraining()
    worker.replay_receipts()
    if stopping():
        raise WorkerDraining()
    # Never send a different capability even if the node is misconfigured.
    response = client.send("claim", {"capabilities": [CAPABILITY]})
    if not isinstance(response, dict):
        raise ProtocolError("claim_response")
    if response.get("state") == "idle":
        return {"state": "idle"}
    job, lease = response.get("job"), response.get("lease_token")
    if (response.get("state") != "claimed" or not isinstance(job, dict)
            or job.get("kind") != CAPABILITY or not isinstance(lease, str) or not lease):
        raise ProtocolError("unexpected_claim")
    receipt = worker.execute(job, lease)
    if stopping():
        raise WorkerDraining()
    ack = worker.acknowledge(receipt)
    return {"state": ack["state"], "job_id": ack["job_id"],
            "server_ack_verified": ack["server_ack_verified"],
            "result_sha256": receipt["result_sha256"]}


def main() -> int:
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    # Restrict both handler discovery and auxiliary receipt hooks.
    os.environ["MSW_RUNTIME_MODE"] = "render-free"
    os.environ["MSW_AUTONOMOUS_AGENT"] = "0"
    directory = Path(os.environ.get("MSW_DATA_DIR", "/tmp/msw-aux-seo"))
    os.environ["MSW_DATA_DIR"] = str(directory)
    try:
        client = RetryAwareClient(os.environ.get("MSW_WP_URL", ""), os.environ.get("MSW_TRANSPORT_URL", ""),
                                  os.environ.get("MSW_NODE", ""), os.environ.get("MSW_SECRET", ""))
        worker = Worker(client, directory, max_seconds=30, memory_mb=256, cancel_check=stop.is_set)
    except (ValueError, OSError, sqlite3.Error) as error:
        emit("configuration_required", error_class=type(error).__name__, queue_consumption=False)
        stop.wait()
        return 0
    emit("started", capability=CAPABILITY, min_poll_seconds=POLL_SECONDS,
         startup_delay_seconds=POLL_SECONDS, scheduler_enabled=False, durable_storage=False)
    # On restart/deploy, wait before contacting the already stressed origin.
    # This is a quiet-start safeguard, not durable shared backoff state.
    if stop.wait(POLL_SECONDS):
        emit("stopped")
        return 0
    failures = 0
    while not stop.is_set():
        try:
            result = step(client, worker, stop.is_set)
            emit(result.pop("state"), **result)
            failures, delay = 0, POLL_SECONDS
        except WorkerDraining:
            break
        except ProtocolError as error:
            emit("protocol_blocked", code=str(error), queue_consumption=False)
            stop.wait()
            break
        except TransportError as error:
            if error.status in (401, 403):
                emit("authentication_blocked", http_status=error.status, queue_consumption=False)
                stop.wait()
                break
            failures += 1
            delay = retry_delay(failures, error.retry_after)
            emit("connection_backoff", code=error.code, http_status=error.status, retry_seconds=delay)
        except Exception as error:
            failures += 1
            delay = retry_delay(failures)
            emit("worker_backoff", error_class=type(error).__name__, retry_seconds=delay)
        stop.wait(delay)
    emit("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
