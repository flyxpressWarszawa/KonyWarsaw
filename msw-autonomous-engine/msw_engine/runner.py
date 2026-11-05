from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import signal
import sqlite3
import time

from .client import Client, TransportError, encode
from .tasks import TaskError, dispatch, handlers
from . import __version__


class WorkerDraining(RuntimeError):
    """Leave an unfinished lease for server-side recovery during shutdown."""


class Journal:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(data_dir, 0o700)
        self.path = data_dir / "receipts.sqlite3"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS receipts (job_id TEXT PRIMARY KEY,payload_sha256 TEXT NOT NULL,lease_token TEXT NOT NULL,result_json TEXT NOT NULL,result_sha256 TEXT NOT NULL,state TEXT NOT NULL,updated_at INTEGER NOT NULL)")
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def lookup(self, job_id: str, digest: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM receipts WHERE job_id=? AND payload_sha256=?", (job_id, digest)).fetchone()
            return dict(row) if row else None

    def save(self, job: dict, lease: str, result: dict) -> dict:
        raw = encode(result).decode()
        if len(raw.encode()) > 524288:
            raw = encode({"ok": False, "error": "result_limit"}).decode()
        digest = hashlib.sha256(raw.encode()).hexdigest()
        values = (job["id"], job["payload_sha256"], lease, raw, digest, "pending", int(time.time()))
        with self.connect() as db:
            old = db.execute("SELECT payload_sha256 FROM receipts WHERE job_id=?", (job["id"],)).fetchone()
            if old and old["payload_sha256"] != job["payload_sha256"]:
                raise TransportError("journal_payload_conflict")
            db.execute("INSERT INTO receipts VALUES(?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET lease_token=excluded.lease_token,result_json=excluded.result_json,result_sha256=excluded.result_sha256,state='pending',updated_at=excluded.updated_at", values)
        return self.lookup(job["id"], job["payload_sha256"])

    def pending(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM receipts WHERE state='pending' ORDER BY updated_at LIMIT 8")]

    def mark(self, job_id: str, state: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE receipts SET state=?,updated_at=? WHERE job_id=?", (state, int(time.time()), job_id))


def child_task(connection, kind: str, payload: dict, directory: str, max_seconds: int, memory_mb: int):
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (max_seconds, max_seconds + 5))
        if memory_mb > 0:
            resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024, memory_mb * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
        data = dispatch(kind, payload, Path(directory))
        result = {"ok": True, "data": data}
        encode(result)  # Refuse NaN, Infinity and non-JSON objects before committing a receipt.
    except TaskError as error:
        result = {"ok": False, "error": str(error)[:80]}
    except Exception as error:
        result = {"ok": False, "error": "task_" + type(error).__name__.lower()[:60]}
    try:
        raw = encode(result)
        if len(raw) > 524288:
            raw = encode({"ok": False, "error": "result_limit"})
        connection.send_bytes(raw)
    finally:
        connection.close()


class Worker:
    def __init__(self, client: Client, data_dir: Path, *, max_seconds: int = 300, memory_mb: int = 2048, cancel_check=None):
        self.client, self.journal = client, Journal(data_dir)
        self.max_seconds = max(5, min(86400, max_seconds))
        self.memory_mb = memory_mb
        self.stopping = False
        self.cancel_check = cancel_check
        self.last_server_contact = 0
        self.last_job_state = "not_run"
        self.cron_state = "not_run"

    def acknowledge(self, receipt: dict) -> dict:
        fields = {key: receipt[key] for key in ("payload_sha256", "lease_token", "result_json", "result_sha256")}
        fields["job_id"] = receipt["job_id"]
        acknowledgement = self.client.send("complete", fields)
        readback = self.client.send("status", {"job_id": receipt["job_id"]})
        digest = receipt["result_sha256"]
        if acknowledgement.get("stored") is not True or acknowledgement.get("result_sha256") != digest or readback.get("result_sha256") != digest or readback.get("state") not in {"completed", "failed"}:
            raise TransportError("result_readback_mismatch")
        self.journal.mark(receipt["job_id"], "acknowledged")
        self.last_server_contact = int(time.time())
        self.last_job_state = readback["state"]
        return {"job_id": receipt["job_id"], "state": readback["state"], "server_ack_verified": True}

    def replay_receipts(self) -> int:
        count = 0
        for receipt in self.journal.pending():
            try:
                self.acknowledge(receipt)
                count += 1
            except TransportError as error:
                if error.status == 409:
                    # A completion can be committed even if its HTTP response was
                    # lost. Verify the durable result before abandoning a receipt.
                    readback = self.client.send("status", {"job_id": receipt["job_id"]})
                    self.last_server_contact = int(time.time())
                    if readback.get("state") in {"completed", "failed"} and readback.get("result_sha256") == receipt["result_sha256"]:
                        self.journal.mark(receipt["job_id"], "acknowledged")
                        self.last_job_state = readback["state"]
                        count += 1
                    else:
                        self.journal.mark(receipt["job_id"], "stale_lease")
                else:
                    raise
        return count

    def execute(self, job: dict, lease: str) -> dict:
        raw = job.get("payload", "")
        if not isinstance(raw, str) or len(raw.encode()) > 262144 or hashlib.sha256(raw.encode()).hexdigest() != job.get("payload_sha256"):
            raise TransportError("job_payload_hash_mismatch")
        payload = json.loads(raw)
        # PHP serializes its empty argument array as []; this one no-argument
        # diagnostic accepts that representation after the raw hash is checked.
        if payload == [] and job.get("kind") == "runtime.catalog":
            payload = {}
        if not isinstance(payload, dict) or job.get("kind") not in handlers():
            raise TransportError("job_capability_mismatch")
        cached = self.journal.lookup(job["id"], job["payload_sha256"])
        if cached:
            result = json.loads(cached["result_json"])
            return self.journal.save(job, lease, result)
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(target=child_task, args=(child, job["kind"], payload, str(self.journal.data_dir), self.max_seconds, self.memory_mb))
        process.start()
        child.close()
        started = time.monotonic()
        heartbeat_at = started
        result = None
        try:
            while process.is_alive() or parent.poll():
                if parent.poll(.2):
                    result = json.loads(parent.recv_bytes(524289))
                    break
                now = time.monotonic()
                if self.stopping or (self.cancel_check is not None and self.cancel_check()):
                    raise WorkerDraining()
                if now - started > self.max_seconds:
                    process.terminate()
                    result = {"ok": False, "error": "task_time_budget"}
                    break
                if now - heartbeat_at >= 60:
                    self.client.send("heartbeat", {"job_id": job["id"], "lease_token": lease, "capabilities": list(handlers())})
                    self.last_server_contact = int(time.time())
                    heartbeat_at = now
            if result is None:
                result = {"ok": False, "error": "task_process_ended"}
        except EOFError:
            result = {"ok": False, "error": "task_process_ended"}
        finally:
            parent.close()
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
        return self.journal.save(job, lease, result)

    def run_once(self) -> dict:
        self.replay_receipts()
        if self.stopping:
            raise WorkerDraining()
        response = self.client.send("claim", {"capabilities": list(handlers())})
        self.last_server_contact = int(time.time())
        if response.get("state") != "claimed":
            return {"state": "idle"}
        job = response.get("job")
        token = response.get("lease_token")
        if not isinstance(job, dict) or not isinstance(token, str):
            raise TransportError("claim_schema_invalid")
        return self.acknowledge(self.execute(job, token))

    def health(self, state: str) -> None:
        value = {"at": int(time.time()), "last_server_contact": self.last_server_contact, "state": state,
                 "capabilities": list(handlers()), "pid": os.getpid(), "worker_version": __version__,
                 "last_job_state": self.last_job_state, "cron_state": self.cron_state}
        target = self.journal.data_dir / "health.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(encode(value))
        temporary.replace(target)

    def loop(self, *, once: bool = False, poke_cron_seconds: int = 300) -> int:
        pause, last_poke = 15, 0.0
        print(json.dumps({"worker_version": __version__, "state": "ready"}), flush=True)
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stopping", True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "stopping", True))
        # Keep cron independent of long model calls, queue errors and idle backoff.
        fleet_thread = None
        if not once and poke_cron_seconds > 0:
            from .fleet_scheduler import FleetScheduler, pump, consume_data
            fleet_thread = FleetScheduler(self.client.wordpress_url, self.journal.data_dir,
                                         maintain=lambda: pump(self.client),
                                         consume=lambda: consume_data(self.client, self.journal.data_dir, lambda: self.stopping)).start(lambda: self.stopping)
        while not self.stopping:
            try:
                result = self.run_once()
                print(json.dumps(result), flush=True)
                pause = min(60, pause + 5) if result["state"] == "idle" else (30 if result["state"] == "failed" else 10)
                self.health(result["state"])
                if fleet_thread is None and poke_cron_seconds > 0 and time.monotonic() - last_poke > poke_cron_seconds:
                    try:
                        self.client.poke_cron()
                        self.cron_state = "requested"
                    except TransportError as error:
                        self.cron_state = "request_failed"
                        print(json.dumps({"state": "scheduler_retry", "code": error.code}), flush=True)
                    last_poke = time.monotonic()
            except WorkerDraining:
                self.health("draining")
                return 0
            except TransportError as error:
                print(json.dumps({"state": "connection_retry", "code": error.code, "http_status": error.status}), flush=True)
                self.health("connection_retry")
                pause = max(error.retry_after, min(300, max(30, pause * 2)))
                if once:
                    return 1
            except Exception as error:
                print(json.dumps({"state": "worker_error", "code": type(error).__name__}), flush=True)
                self.health("worker_error")
                pause = min(300, max(30, pause * 2))
                if once:
                    return 1
            if once:
                return 0
            deadline = time.monotonic() + pause
            while not self.stopping and time.monotonic() < deadline:
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        return 0


