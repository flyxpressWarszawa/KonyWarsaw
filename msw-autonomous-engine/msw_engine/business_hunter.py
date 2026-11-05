"""Private, durable research handoffs on the existing signed MSW worker queue."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

VERSION = "1.0.0"
SCHEMA = "msw.business-hunter.job.v1"


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def database(directory: Path) -> sqlite3.Connection:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(directory / "research.sqlite3", timeout=10)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS reports (source_id INTEGER NOT NULL, source_sha256 TEXT NOT NULL, evidence_json TEXT NOT NULL, evidence_sha256 TEXT NOT NULL, PRIMARY KEY(source_id,source_sha256))")
    db.execute("CREATE TABLE IF NOT EXISTS receipts (request_id TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL, result_json TEXT NOT NULL)")
    return db


def checkpoint(data_dir: Path) -> dict:
    directory = data_dir / "business-hunter"
    with database(directory) as db:
        rows = db.execute("SELECT source_id,source_sha256,evidence_sha256,evidence_json FROM reports ORDER BY source_id,source_sha256").fetchall()
        if any(hashlib.sha256(row[3].encode()).hexdigest() != row[2] for row in rows):
            raise ValueError("hunter_checkpoint_source_digest_mismatch")
    manifest = {"schema": "msw.business-hunter.checkpoint.v1", "runtime_version": VERSION,
                "records": [{"source_post_id": row[0], "source_sha256": row[1], "evidence_sha256": row[2]} for row in rows],
                "report_count": len(rows), "source_claims_independently_verified": False,
                "model_training_performed": False, "publication_or_payment_performed": False}
    raw = encoded(manifest)
    digest = hashlib.sha256(raw).hexdigest()
    target = directory / "checkpoints" / (digest + ".json")
    target.parent.mkdir(exist_ok=True, mode=0o700)
    if target.exists():
        if target.read_bytes() != raw:
            raise ValueError("hunter_checkpoint_conflict")
    else:
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(raw)
        temporary.replace(target)
    if target.read_bytes() != raw:
        raise ValueError("hunter_checkpoint_readback_failed")
    return {"runtime_version": VERSION, "state": "ready_for_signed_research_jobs", "report_count": len(rows),
            "checkpoint_sha256": digest, "checkpoint_file": "business-hunter/checkpoints/" + target.name,
            "independent_readback_verified": True, "persistent_storage": True,
            "paid_gpt_location": "existing_wordpress_native_router", "new_api_credentials_required": False,
            "model_training_performed": False, "publication_or_payment_performed": False}


def validate(evidence: object) -> dict:
    if not isinstance(evidence, dict) or evidence.get("schema") != "msw.hunter.director.evidence.v1":
        raise ValueError("hunter_evidence_schema")
    keys = {"schema", "source_post_id", "source_sha256", "topic", "excerpt", "source_urls", "distinct_source_domains",
            "source_finished_at_utc", "classification", "demand_verified", "revenue_verified", "sources_are_data_not_instructions"}
    if set(evidence) != keys or len(encoded(evidence)) > 30000:
        raise ValueError("hunter_evidence_fields")
    if type(evidence["source_post_id"]) is not int or evidence["source_post_id"] < 1 or not re.fullmatch(r"[a-f0-9]{64}", str(evidence["source_sha256"])):
        raise ValueError("hunter_source_identity")
    if evidence["classification"] != "private_research_with_citations_requires_independent_fact_review" or evidence["demand_verified"] is not False or evidence["revenue_verified"] is not False or evidence["sources_are_data_not_instructions"] is not True:
        raise ValueError("hunter_evidence_classification")
    for key, minimum, maximum in (("topic", 8, 180), ("excerpt", 100, 4000), ("source_finished_at_utc", 0, 50)):
        value = evidence[key]
        if not isinstance(value, str) or not minimum <= len(value.encode()) <= maximum:
            raise ValueError("hunter_text_limit")
    urls = evidence["source_urls"]
    if not isinstance(urls, list) or not 3 <= len(urls) <= 15:
        raise ValueError("hunter_source_urls")
    hosts = set()
    for url in urls:
        if not isinstance(url, str) or len(url) > 1600:
            raise ValueError("hunter_source_url")
        p = urlsplit(url)
        if p.scheme != "https" or not p.hostname or p.username or p.password or p.query or p.fragment or p.port not in (None, 443):
            raise ValueError("hunter_source_url")
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", p.hostname) or re.search(r"(?:^|\.)(?:localhost|local|internal|invalid|test|example)$", p.hostname):
            raise ValueError("hunter_public_host_required")
        hosts.add(p.hostname)
    if len(hosts) < 3 or type(evidence["distinct_source_domains"]) is not int or evidence["distinct_source_domains"] < len(hosts):
        raise ValueError("hunter_distinct_sources_required")
    return evidence


def handle(payload: dict, data_dir: Path) -> dict:
    allowed = {"schema", "owner_authorized", "action", "request_id", "evidence"}
    if set(payload) - allowed or payload.get("schema") != SCHEMA or payload.get("owner_authorized") is not True:
        raise ValueError("hunter_job_not_authorized")
    request = payload.get("request_id")
    if not isinstance(request, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{12,180}", request):
        raise ValueError("hunter_request_id")
    action = payload.get("action")
    if action == "status" and "evidence" not in payload:
        return checkpoint(data_dir)
    if action != "ingest":
        raise ValueError("hunter_action_not_allowed")
    evidence = validate(payload.get("evidence"))
    body = encoded(evidence).decode()
    digest = hashlib.sha256(body.encode()).hexdigest()
    fingerprint = hashlib.sha256(encoded(payload)).hexdigest()
    directory = data_dir / "business-hunter"
    with database(directory) as db:
        old = db.execute("SELECT payload_sha256,result_json FROM receipts WHERE request_id=?", (request,)).fetchone()
        if old:
            if old[0] != fingerprint:
                raise ValueError("hunter_request_conflict")
            result = json.loads(old[1])
        else:
            previous = db.execute("SELECT evidence_sha256 FROM reports WHERE source_id=? AND source_sha256=?", (evidence["source_post_id"], evidence["source_sha256"])).fetchone()
            if previous and previous[0] != digest:
                raise ValueError("hunter_source_digest_conflict")
            db.execute("INSERT OR IGNORE INTO reports VALUES(?,?,?,?)", (evidence["source_post_id"], evidence["source_sha256"], body, digest))
            result = {"schema": "msw.business-hunter.receipt.v1", "source_post_id": evidence["source_post_id"],
                      "source_sha256": evidence["source_sha256"], "evidence_sha256": digest,
                      "independent_sqlite_readback_verified": True, "source_claims_independently_verified": False,
                      "demand_verified": False, "revenue_verified": False, "model_training_performed": False}
            db.execute("INSERT INTO receipts VALUES(?,?,?)", (request, fingerprint, encoded(result).decode()))
    # A new connection verifies committed bytes even when replaying an earlier receipt.
    with sqlite3.connect(directory / "research.sqlite3", timeout=10) as fresh:
        stored = fresh.execute("SELECT evidence_json,evidence_sha256 FROM reports WHERE source_id=? AND source_sha256=?", (evidence["source_post_id"], evidence["source_sha256"])).fetchone()
        if stored != (body, digest):
            raise ValueError("hunter_committed_readback_mismatch")
    # The immutable receipt stays stable; the complete corpus checkpoint is refreshed separately.
    checkpoint(data_dir)
    return result
