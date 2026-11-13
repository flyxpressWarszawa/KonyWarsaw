"""Low-rate SEO and evidence-ranking consumer for a scoped WP node.

Only deterministic seo.analyze and research.rank jobs are claimed. No scheduler, publication,
AI call, satellite operation, or private knowledge job is started here. The
journal and retry deadline use MSW_DATA_DIR; restart persistence requires that
directory to be on a persistent volume. An interrupted SEO calculation may be
recomputed when its server-side lease expires, with the same receipt contract.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import tempfile
import threading
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .client import Client, TransportError, encode, signed_headers
from .recovery import OriginPaused, protected_open
from .runner import Worker, WorkerDraining

CAPABILITY = "seo.analyze"
CAPABILITIES = ("seo.analyze", "research.rank")
POLL_SECONDS = 3600
WARMUP_POLL_SECONDS = 60
LOW_SAMPLE_RECHECK_SECONDS = 25
MAX_JOBS_PER_CYCLE = 4
MAX_RECEIPT_REPLAYS_PER_CYCLE = 1
MAX_RETRY_SECONDS = 24 * 60 * 60
MAX_SERVER_WAIT_SECONDS = 7 * 24 * 60 * 60


class ProtocolError(Exception):
    """An unexpected queue response must stop this consumer's requests."""


class AuxiliaryBackoff:
    """Persist the auxiliary retry deadline without altering shared transport state.

    Only timing metadata is stored. Corrupt or unwritable state stops this
    consumer; it never falls back to an immediate request after a restart.
    This is a checkpoint for the existing single-instance worker, not a lock
    or a shared scheduler for several nodes.
    """

    def __init__(self, directory: Path, *, clock=None):
        self.path = directory / "auxiliary-backoff.json"
        self.clock = clock or time.time

    def _load(self) -> tuple[int, int]:
        if self.path.is_symlink():
            raise ValueError("backoff_state_symlink")
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(1025)
        except FileNotFoundError:
            return 0, 0
        def unique_fields(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("backoff_duplicate_field")
                value[key] = item
            return value
        value = json.loads(raw, object_pairs_hook=unique_fields) if len(raw) <= 1024 else None
        if (not isinstance(value, dict) or set(value) != {"version", "failures", "not_before"}
                or type(value["version"]) is not int or value["version"] != 1
                or type(value["failures"]) is not int or not 0 <= value["failures"] <= 5
                or type(value["not_before"]) is not int or not 0 <= value["not_before"] <= 2 ** 53):
            raise ValueError("backoff_state_invalid")
        remaining = max(0, math.ceil(value["not_before"] - self.clock()))
        if remaining > MAX_SERVER_WAIT_SECONDS + 1:
            raise ValueError("backoff_deadline_invalid")
        return value["failures"], value["not_before"]

    def read(self) -> tuple[int, int]:
        failures, deadline = self._load()
        return failures, max(0, math.ceil(deadline - self.clock()))

    def save(self, failures: int, delay: int) -> None:
        if (type(failures) is not int or failures < 0 or type(delay) is not int
                or not 0 <= delay <= MAX_SERVER_WAIT_SECONDS):
            raise ValueError("backoff_state_invalid")
        _, previous_deadline = self._load()
        # Never shorten an already committed server wait, including on restart.
        value = {"version": 1, "failures": min(5, failures),
                 "not_before": max(previous_deadline, math.ceil(self.clock() + delay))}
        descriptor, temporary = tempfile.mkstemp(prefix=".aux-backoff-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encode(value))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


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
    # One failed call causes at least one hour of silence; repeated failures
    # exponentially back off to one day. A longer server wait takes precedence.
    return max(retry_after, min(MAX_RETRY_SECONDS, POLL_SECONDS * 2 ** min(max(failures - 1, 0), 4)))


def next_poll_delay(state: str, low_samples: int) -> int:
    """Recheck one low sample before its 90-second freshness window expires."""
    if state == "origin_warmup":
        return LOW_SAMPLE_RECHECK_SECONDS if low_samples == 1 else WARMUP_POLL_SECONDS
    return POLL_SECONDS


def read_origin_health(client: Client) -> dict:
    """Read only the existing signed, account-scoped Doctor telemetry."""
    raw = encode({"schema": "msw.render.account-health.v1"})
    request = Request(client.wordpress_url + "/wp-content/plugins/msw-unified-engine/msw-render-account-health.php",
                      data=raw, headers=signed_headers("status", client.node, client.secret, raw), method="POST")
    try:
        with protected_open(client.opener, request, timeout=12) as response:
            body = response.read(8193)
        if len(body) > 8192:
            return {"state": "unavailable"}
        result = json.loads(body)
        return result if isinstance(result, dict) else {"state": "unavailable"}
    except HTTPError as error:
        wait = parse_retry_after(error.headers.get("Retry-After") if error.headers else None) or 0
        failure = TransportError("account_health_http", error.code, wait)
        # TransportError's constructor caps at 900s. Preserve longer server waits.
        failure.retry_after = max(failure.retry_after, wait)
        raise failure from None
    except OriginPaused as error:
        raise TransportError("account_health_circuit_open", 0, error.retry_after) from None
    except (URLError, TimeoutError, OSError, ValueError, TypeError, sqlite3.Error):
        return {"state": "unavailable"}


class OriginPressureGate:
    """Require two distinct low samples initially and after pressure or gaps."""

    def __init__(self, *, reader=read_origin_health, clock=time.time):
        self.reader, self.clock = reader, clock
        self.open = False
        self.low_samples = 0
        self.last_at = None

    def check(self, client: Client) -> dict:
        try:
            value = self.reader(client)
        except Exception:
            # A transport error is a telemetry gap, not evidence that the
            # previous healthy sample is still safe for a later claim.
            self.open, self.low_samples, self.last_at = False, 0, None
            raise
        if not isinstance(value, dict):
            value = {}
        at, cpu, ram = value.get("measured_at"), value.get("cpu_percent"), value.get("physical_memory_percent")
        now = int(self.clock())
        previous_sample_age = None if self.last_at is None else now - self.last_at
        sample_window_reset = previous_sample_age is not None and previous_sample_age > 90
        if sample_window_reset:
            self.open, self.low_samples, self.last_at = False, 0, None
        valid = (value.get("schema") == "msw.render.account-health.v1" and value.get("state") == "fresh"
                 and type(at) is int and 0 <= now - at <= 90
                 and type(cpu) in (int, float) and type(ram) in (int, float)
                 and math.isfinite(cpu) and math.isfinite(ram) and 0 <= cpu <= 100 and 0 <= ram <= 100)
        if not valid or (self.last_at is not None and at < self.last_at):
            self.open, self.low_samples, self.last_at = False, 0, None
            return {"state": "origin_unknown"}
        if cpu >= 70 or ram >= 70:
            self.open, self.low_samples, self.last_at = False, 0, at
            # A fresh CPU-only pressure reading permits one remote computation.
            # The signed queue still costs one claim and its receipt requests.
            if cpu >= 70 and ram < 70:
                return {"state": "pressure_offload", "cpu_percent": cpu,
                        "physical_memory_percent": ram}
            return {"state": "origin_pressure", "cpu_percent": cpu, "physical_memory_percent": ram}
        if not self.open:
            if cpu >= 60 or ram >= 60:
                self.low_samples, self.last_at = 0, at
            elif at != self.last_at:
                self.low_samples, self.last_at = self.low_samples + 1, at
            if self.low_samples < 2:
                return {"state": "origin_warmup", "low_samples": self.low_samples,
                        "sample_age_seconds": now - at,
                        "previous_sample_age_seconds": previous_sample_age,
                        "sample_window_reset": sample_window_reset}
            self.open = True
        self.last_at = at
        return {"state": "ready"}


def step(client: Client, worker: Worker, stopping=lambda: False, *, gate: OriginPressureGate,
         replay_pending: bool = True) -> dict:
    if stopping():
        raise WorkerDraining()
    if replay_pending:
        worker.replay_receipts(limit=MAX_RECEIPT_REPLAYS_PER_CYCLE)
    if stopping():
        raise WorkerDraining()
    health = gate.check(client)
    if health.get("state") not in ("ready", "pressure_offload"):
        return health
    # Never request a capability outside this narrow, local-only allowlist.
    response = client.send("claim", {"capabilities": list(CAPABILITIES)})
    if not isinstance(response, dict):
        raise ProtocolError("claim_response")
    if response.get("state") == "idle":
        return {"state": "idle"}
    job, lease = response.get("job"), response.get("lease_token")
    if (response.get("state") != "claimed" or not isinstance(job, dict)
            or job.get("kind") not in CAPABILITIES or not isinstance(lease, str) or not lease):
        raise ProtocolError("unexpected_claim")
    receipt = worker.execute(job, lease)
    if stopping():
        raise WorkerDraining()
    ack = worker.acknowledge(receipt)
    return {"state": ack["state"], "job_id": ack["job_id"],
            "server_ack_verified": ack["server_ack_verified"],
            "result_sha256": receipt["result_sha256"]}


def batch_entries(response: dict, *, limit: int = MAX_JOBS_PER_CYCLE) -> list[dict]:
    """Validate the entire bounded admission before executing any payload."""
    if not isinstance(response, dict):
        raise ProtocolError("claim_response")
    state = response.get("state")
    if state == "idle":
        if response.get("jobs") or response.get("job"):
            raise ProtocolError("idle_contains_jobs")
        return []
    if state == "claimed":
        # Safe rolling upgrade/rollback: an old transport can return one job.
        entries = [response]
    elif state == "claimed_batch" and response.get("batch_protocol") == "msw.seo.batch.v1":
        entries = response.get("jobs")
    else:
        raise ProtocolError("unexpected_claim")
    if not isinstance(entries, list) or not 1 <= len(entries) <= limit:
        raise ProtocolError("batch_size")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ProtocolError("batch_entry")
        job, lease = entry.get("job"), entry.get("lease_token")
        if (not isinstance(job, dict) or job.get("kind") not in CAPABILITIES
                or not isinstance(lease, str) or not 1 <= len(lease) <= 256):
            raise ProtocolError("unexpected_claim")
        if state == "claimed_batch" and job["kind"] != CAPABILITY:
            raise ProtocolError("batch_scope")
        if state == "claimed_batch" and (type(entry.get("lease_seconds")) is not int
                                          or entry["lease_seconds"] != 900):
            raise ProtocolError("batch_lease_window")
        ident, payload = job.get("id"), job.get("payload")
        if (not isinstance(ident, str) or not 1 <= len(ident) <= 128 or ident in seen
                or not isinstance(payload, str) or len(payload.encode()) > 262144
                or hashlib.sha256(payload.encode()).hexdigest() != job.get("payload_sha256")):
            raise ProtocolError("batch_job_integrity")
        seen.add(ident)
    return entries


def run_cycle(client: Client, worker: Worker, gate: OriginPressureGate, stop) -> str:
    """Admit a bounded SEO batch only when the origin is healthy.

    Under CPU pressure, preserve the one-claim budget. If a healthy origin has
    no SEO work, use one legacy mixed claim so ranking still makes progress.
    """
    if stop.is_set():
        raise WorkerDraining()
    worker.replay_receipts(limit=MAX_RECEIPT_REPLAYS_PER_CYCLE)
    if stop.is_set():
        raise WorkerDraining()
    health = gate.check(client)
    if health.get("state") not in ("ready", "pressure_offload"):
        state = health.pop("state", "origin_unknown")
        emit(state, **health)
        return state
    if stop.is_set():
        raise WorkerDraining()
    claim_requests = 1
    if health["state"] == "ready":
        response = client.send("claim", {"capabilities": [CAPABILITY], "batch_limit": MAX_JOBS_PER_CYCLE})
        entries = batch_entries(response)
        if not entries:
            if stop.is_set():
                raise WorkerDraining()
            response = client.send("claim", {"capabilities": list(CAPABILITIES)})
            claim_requests = 2
            entries = batch_entries(response, limit=1)
    else:
        response = client.send("claim", {"capabilities": list(CAPABILITIES)})
        entries = batch_entries(response, limit=1)
    if not entries:
        emit("idle")
        return "idle"
    emit("batch_admitted", jobs=len(entries), protocol=response.get("batch_protocol", "legacy_single"))
    failed = 0
    for index, entry in enumerate(entries):
        if stop.is_set():
            raise WorkerDraining()
        receipt = worker.execute(entry["job"], entry["lease_token"])
        if stop.is_set():
            raise WorkerDraining()
        ack = worker.acknowledge(receipt)
        failed += ack["state"] != "completed"
        emit(ack["state"], job_id=ack["job_id"], server_ack_verified=ack["server_ack_verified"],
             result_sha256=receipt["result_sha256"], batch_index=index + 1, batch_size=len(entries))
        if index + 1 < len(entries) and stop.wait(3):
            raise WorkerDraining()
    emit("batch_finished", admitted=len(entries), completed=len(entries) - failed, failed=failed,
         claim_requests=claim_requests)
    return "failed" if failed else "completed"


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
        backoff = AuxiliaryBackoff(directory)
        failures, remaining = backoff.read()
        startup_delay = max(POLL_SECONDS, remaining)
    except (ValueError, OSError, sqlite3.Error) as error:
        emit("configuration_required", error_class=type(error).__name__, queue_consumption=False)
        stop.wait()
        return 0
    emit("started", capabilities=list(CAPABILITIES), min_poll_seconds=POLL_SECONDS,
         startup_delay_seconds=startup_delay, low_sample_recheck_seconds=LOW_SAMPLE_RECHECK_SECONDS,
         scheduler_enabled=False, backoff_storage="MSW_DATA_DIR",
         max_receipt_replays_per_cycle=MAX_RECEIPT_REPLAYS_PER_CYCLE,
         max_jobs_per_cycle=MAX_JOBS_PER_CYCLE, admission_protocol="seo_batch_then_legacy_fallback")
    # On restart/deploy, wait before contacting the already stressed origin.
    # A previously committed Retry-After can require a longer quiet start.
    if stop.wait(startup_delay):
        emit("stopped")
        return 0
    gate = OriginPressureGate()
    while not stop.is_set():
        try:
            state = run_cycle(client, worker, gate, stop)
            failures = 0
            delay = next_poll_delay(state, gate.low_samples)
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
        try:
            backoff.save(failures, delay)
        except (OSError, ValueError) as error:
            emit("backoff_storage_blocked", error_class=type(error).__name__, queue_consumption=False)
            stop.wait()
            break
        stop.wait(delay)
    emit("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


