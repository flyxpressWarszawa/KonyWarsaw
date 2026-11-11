"""Durable, fail-closed reservation for potentially billed AI work.

An uncertain attempt is never automatically repeated, even after worker restart,
lost heartbeat, or a new server lease. Existing verified receipts may be replayed
without invoking inference a second time. No secret or customer content is stored.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

from .client import TransportError


PAID_CAPABILITIES = frozenset({"llm.generate"})
# Serialize local schema creation; SQLite busy_timeout handles separate processes.
_SCHEMA_LOCK = threading.Lock()


class PaidAttemptGuard:
    def __init__(self, directory: Path):
        self.path = directory / "paid-attempt-guards.sqlite3"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _SCHEMA_LOCK:
            db = self.connect()
            try:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS attempts ("
                    "request_hash TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL, "
                    "job_hash TEXT NOT NULL, state TEXT NOT NULL, started_at INTEGER NOT NULL)"
                )
                db.commit()
            finally:
                db.close()
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("PRAGMA busy_timeout=15000")
        # Never attempt WAL journal-mode transitions on every concurrent open.
        # BEGIN IMMEDIATE and a busy timeout provide an atomic, bounded guard.
        return db

    def reserve(self, job: dict, payload: dict) -> None:
        """Atomically deny duplicates, including a second job with the same request ID."""
        ident = payload.get("request_id")
        if isinstance(ident, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{8,120}", ident):
            scope = "llm.generate:request:" + ident
        else:
            # Missing/invalid request_id does not permit bypassing the ledger.
            scope = "llm.generate:job:" + str(job["id"])
        request_hash = hashlib.sha256(scope.encode("utf-8")).hexdigest()
        digest = str(job["payload_sha256"])
        job_hash = hashlib.sha256(str(job["id"]).encode("utf-8")).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT payload_sha256 FROM attempts WHERE request_hash=?", (request_hash,)
            ).fetchone()
            if existing is not None:
                if existing[0] != digest:
                    raise TransportError("paid_attempt_payload_conflict")
                # No automatic retry or bypass even if a later lease is returned.
                raise TransportError("paid_attempt_outcome_unverified")
            db.execute(
                "INSERT INTO attempts (request_hash,payload_sha256,job_hash,state,started_at) "
                "VALUES (?,?,?,'in_flight',?)",
                (request_hash, digest, job_hash, int(time.time())),
            )
        # SQLite commit is completed before the subprocess can be started.
