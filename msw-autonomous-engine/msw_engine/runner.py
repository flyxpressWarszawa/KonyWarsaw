from __future__ import annotations

from contextlib import closing
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
from .lifecycle import drain_requested, finish_threads, write_status


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
        values = (job["id"], job["payload_sha256"], lease, raw, digest, "ready_to_complete", int(time.time()))
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT payload_sha256,result_sha256 FROM receipts WHERE job_id=?", (job["id"],)).fetchone()
            if old and old["payload_sha256"] != job["payload_sha256"]:
                raise TransportError("journal_payload_conflict")
            if old and old["result_sha256"] != digest:
                raise TransportError("journal_result_conflict")
            db.execute(
                "INSERT INTO receipts VALUES(?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
                "lease_token=excluded.lease_token,updated_at=excluded.updated_at WHERE receipts.state='ready_to_complete'",
                values,
            )
            return dict(db.execute("SELECT * FROM receipts WHERE job_id=?", (job["id"],)).fetchone())

    def reserve_completion(self, receipt: dict) -> bool:
        """Commit the send intent before the request, once across all worker lanes."""
        with closing(self.connect()) as db, db:
            return db.execute(
                "UPDATE receipts SET state='completion_attempted',updated_at=? WHERE job_id=? AND payload_sha256=? "
                "AND result_sha256=? AND lease_token=? AND state='ready_to_complete'",
                (int(time.time()), receipt["job_id"], receipt["payload_sha256"], receipt["result_sha256"], receipt["lease_token"]),
            ).rowcount == 1

    def pending(self) -> list[dict]:
        with closing(self.connect()) as db:
            # Existing pending rows can predate an attempted send. Recover them by status only.
            return [dict(row) for row in db.execute(
                "SELECT * FROM receipts WHERE state IN ('ready_to_complete','completion_attempted','pending') "
                "OR (state IN ('readback_deferred','pending_deferred') AND updated_at<=?) ORDER BY updated_at LIMIT 8",
                (int(time.time()) - 3600,))]

    def mark(self, receipt: dict, state: str) -> None:
        with closing(self.connect()) as db, db:
            db.execute(
                "UPDATE receipts SET state=?,updated_at=? WHERE job_id=? AND payload_sha256=? AND result_sha256=? "
                "AND state<>'acknowledged' AND (state<>'ready_to_complete' OR ?='acknowledged')",
                (state, int(time.time()), receipt["job_id"], receipt["payload_sha256"], receipt["result_sha256"], state),
            )


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
        self.background_threads = []

    def admission_stopped(self):
        return self.stopping or drain_requested(self.journal.data_dir)

    def check_admission(self):
        self.check_draining()
        if drain_requested(self.journal.data_dir):
            raise WorkerDraining()

    def check_draining(self) -> None:
        if self.stopping or (self.cancel_check is not None and self.cancel_check()):
            raise WorkerDraining()

    def acknowledge(self, receipt: dict) -> dict:
        self.check_draining()
        if self.journal.reserve_completion(receipt):
            fields = {key: receipt[key] for key in ("job_id", "payload_sha256", "lease_token", "result_json", "result_sha256")}
            self.check_draining()
            try:
                acknowledgement = self.client.send("complete", fields)
            except TransportError as error:
                if error.status != 409:
                    raise
            else:
                if acknowledgement.get("stored") is not True or acknowledgement.get("result_sha256") != receipt["result_sha256"]:
                    raise TransportError("completion_response_mismatch")

        try:
            readback = self.client.send("status", {"job_id": receipt["job_id"]})
        except TransportError as error:
            if error.status != 404:
                raise
            return self.defer_confirmation(receipt, "status_not_found")
        self.last_server_contact = int(time.time())
        if readback.get("state") not in {"completed", "failed"}:
            return self.defer_confirmation(receipt, "status_not_terminal")
        if (readback.get("id") != receipt["job_id"]
                or readback.get("payload_sha256") != receipt["payload_sha256"]
                or readback.get("result_sha256") != receipt["result_sha256"]):
            return self.defer_confirmation(receipt, "result_readback_mismatch")

        self.journal.mark(receipt, "acknowledged")
        self.last_job_state = readback["state"]
        if (getattr(self.client, "wordpress_url", "") == "https://mobilny-serwis-warszawa.pl"
                and os.environ.get("MSW_AUTONOMOUS_AGENT", "1") == "1"):
            from .wordpress_agent import record_native_receipt
            try:
                record_native_receipt(self.journal.data_dir, receipt)
            except (ValueError, OSError, sqlite3.Error):
                # The remote ACK is already verified. Never repeat native work
                # because an auxiliary coverage ledger could not be written.
                print(json.dumps({"autonomous_agent": {"state": "native_ledger_unavailable"}}), flush=True)
        return {"job_id": receipt["job_id"], "state": readback["state"], "server_ack_verified": True}

    def defer_confirmation(self, receipt: dict, reason: str) -> dict:
        self.journal.mark(receipt, "readback_deferred")
        self.last_job_state = "ack_pending"
        print(json.dumps({"receipt_recovery": {"state": "deferred_unverified", "reason": reason, "retry_seconds": 3600}}), flush=True)
        return {"job_id": receipt["job_id"], "state": "ack_pending", "server_ack_verified": False}

    def replay_receipts(self) -> int:
        count = 0
        for receipt in self.journal.pending():
            if self.acknowledge(receipt)["server_ack_verified"]:
                count += 1
        return count

    def execute(self, job: dict, lease: str) -> dict:
        self.check_draining()
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
        try:
            process = context.Process(target=child_task, args=(child, job["kind"], payload, str(self.journal.data_dir), self.max_seconds, self.memory_mb))
            self.check_draining()
            process.start()
        except BaseException:
            parent.close()
            raise
        finally:
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
                self.check_draining()
                if now - started > self.max_seconds:
                    process.terminate()
                    result = {"ok": False, "error": "task_time_budget"}
                    break
                if now - heartbeat_at >= 60:
                    self.client.send("heartbeat", {"job_id": job["id"], "lease_token": lease, "capabilities": list(handlers())})
                    self.last_server_contact = int(time.time())
                    heartbeat_at = now
            if result is None:
                self.check_draining()
                result = {"ok": False, "error": "task_process_ended"}
        except EOFError:
            self.check_draining()
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
        self.check_admission()
        self.replay_receipts()
        capabilities = list(handlers())
        self.check_admission()
        response = self.client.send("claim", {"capabilities": capabilities})
        self.last_server_contact = int(time.time())
        self.check_draining()
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
        try:
            return self._loop(once=once, poke_cron_seconds=poke_cron_seconds)
        finally:
            # All long-lived lanes must finish their admitted step and durable writes.
            self._closing = True
            remaining = finish_threads(self.background_threads, self.journal.data_dir,
                                       lambda: self.stopping or not drain_requested(self.journal.data_dir))
            write_status(self.journal.data_dir, 'incomplete' if remaining else 'drained',
                         remaining_threads=remaining, hard_stop=self.stopping)
            if remaining:
                return 3
            # Keep a drained service alive so Render cannot restart old code before cutover.
            while not self.stopping and drain_requested(self.journal.data_dir):
                time.sleep(0.2)

    def lane_stopping(self):
        return self.admission_stopped() or getattr(self, '_closing', False)

    def track(self, threads):
        self.background_threads.extend(threads if isinstance(threads, list) else [threads])
        return threads

    def _loop(self, *, once: bool = False, poke_cron_seconds: int = 300) -> int:
        pause, last_poke = 15, 0.0
        print(json.dumps({"worker_version": __version__, "state": "ready"}), flush=True)
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stopping", True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "stopping", True))
        if self.admission_stopped():
            return 0
        write_status(self.journal.data_dir, 'running', remaining_threads=[])
        # Catalogue scheduling shares this worker and its existing persistent disk.
        if (not once and os.environ.get("MSW_BUSINESS_BRIDGE", "0") == "1"
                and getattr(self.client, "wordpress_url", "") == "https://mobilny-serwis-warszawa.pl"):
            from .business_bridge import BusinessBridge
            self.track(BusinessBridge(self.client, self.journal.data_dir).start(self.lane_stopping))
        if (not once and os.environ.get("MSW_CATALOG_PUBLISHER", "1") == "1"
                and getattr(self.client, "wordpress_url", "") == "https://mobilny-serwis-warszawa.pl"):
            from .catalog_publisher import CatalogPublisher
            self.track(CatalogPublisher(self.client, self.journal.data_dir).start(self.lane_stopping))
        # Keep cron independent of long model calls, queue errors and idle backoff.
        fleet_thread = None
        if not once and poke_cron_seconds > 0:
            from .fleet_scheduler import FleetScheduler, pump, consume_data
            fleet_thread = self.track(FleetScheduler(self.client.wordpress_url, self.journal.data_dir,
                                         maintain=lambda: pump(self.client),
                                         consume=lambda: consume_data(self.client, self.journal.data_dir, lambda: self.stopping, self.lane_stopping)).start(self.lane_stopping))
        # AI generation must not wait behind thousands of native callbacks.
        # This consumes the SAME server queue with an explicit capability filter.
        if not once and "llm.generate" in handlers():
            from .priority_lane import PriorityLane
            self.track(PriorityLane(self.client, self.journal.data_dir).start(self.lane_stopping, lambda: self.stopping))
        if not once and os.environ.get("MSW_FOTOATLAS_ENABLED") == "1":
            from .fotoatlas_render_bridge import AtlasClient, FotoAtlas
            self.track(FotoAtlas(AtlasClient(self.client), self.journal.data_dir).start(self.lane_stopping))
        if (not once and os.environ.get("MSW_AUTONOMOUS_AGENT", "1") == "1"
                and getattr(self.client, "wordpress_url", "") == "https://mobilny-serwis-warszawa.pl"
                and os.environ.get("MSW_LLM_PROVIDER") == "wordpress"):
            from .autonomous_agent import AutonomousAgent
            try:
                self.track(AutonomousAgent(self.journal.data_dir).start(self.lane_stopping))
            except (OSError, ValueError, sqlite3.Error) as error:
                print(json.dumps({"autonomous_agent": {"state": "startup_blocked", "code": type(error).__name__}}), flush=True)
        while not self.admission_stopped():
            try:
                result = self.run_once()
                print(json.dumps(result), flush=True)
                pause = min(60, pause + 5) if result["state"] == "idle" else (30 if result["state"] == "failed" else 10)
                self.health(result["state"])
                if fleet_thread is None and poke_cron_seconds > 0 and time.monotonic() - last_poke > poke_cron_seconds:
                    self.check_admission()
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
            while not self.admission_stopped() and time.monotonic() < deadline:
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        return 0



