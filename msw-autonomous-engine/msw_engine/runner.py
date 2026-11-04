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
    def __init__(self, client: Client, data_dir: Path, *, max_seconds: int = 300, memory_mb: int = 2048):
        self.client, self.journal = client, Journal(data_dir)
        self.max_seconds = max(5, min(86400, max_seconds))
        self.memory_mb = memory_mb
        self.stopping = False
        self.last_server_contact = 0

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
        return {"job_id": receipt["job_id"], "state": readback["state"], "server_ack_verified": True}

    def replay_receipts(self) -> int:
        count = 0
        for receipt in self.journal.pending():
            try:
                self.acknowledge(receipt)
                count += 1
            except TransportError as error:
                if error.status == 409:
                    self.journal.mark(receipt["job_id"], "stale_lease")
                else:
                    raise
        return count

    def execute(self, job: dict, lease: str) -> dict:
        raw = job.get("payload", "")
        if not isinstance(raw, str) or len(raw.encode()) > 262144 or hashlib.sha256(raw.encode()).hexdigest() != job.get("payload_sha256"):
            raise TransportError("job_payload_hash_mismatch")
        payload = json.loads(raw)
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
                 "capabilities": list(handlers()), "pid": os.getpid()}
        target = self.journal.data_dir / "health.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(encode(value))
        temporary.replace(target)

    def loop(self, *, once: bool = False, poke_cron_seconds: int = 300) -> int:
        pause, last_poke = 30, 0.0
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stopping", True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "stopping", True))
        while not self.stopping:
            try:
                result = self.run_once()
                print(json.dumps(result), flush=True)
                pause = min(300, pause + 15) if result["state"] == "idle" else 1
                self.health(result["state"])
                if poke_cron_seconds > 0 and time.monotonic() - last_poke > poke_cron_seconds:
                    self.client.poke_cron()
                    last_poke = time.monotonic()
            except TransportError as error:
                print(json.dumps({"state": "connection_retry", "code": error.code, "http_status": error.status}), flush=True)
                self.health("connection_retry")
                pause = min(300, max(30, pause * 2))
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
