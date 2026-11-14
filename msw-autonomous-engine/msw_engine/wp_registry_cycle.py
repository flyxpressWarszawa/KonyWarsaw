"""Bounded, restart-safe registry mirror in the existing Hermes staging loop.

Metadata only. No AI/provider calls, new scheduler, chat claims, or post edits.
A stale source is a blocked observation, never refreshed by changing its date.
"""
from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timezone, timedelta
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

VERSION = "0.3.1"
SERVICE = "srv-dbnodkbrjlhs739drc5g"
MOUNT = Path("/tmp/msw-aux-seo")
ROOT = MOUNT / "wp-registry-cycle"
SOURCE_URL = ("https://api.github.com/repos/flyxpressWarszawa/signal-harbor-companion/contents/"
              "knowledge/hermes-api-registry/checkpoint-2026-11-14-30-tasks.json?ref=main")
WP_URL = "https://msw-ai.com/wp-json/msw-hermes-registry/v1/snapshot"
SCHEMA = "msw.hermes.wp-ai-registry.snapshot.v1"
SOURCE_SCHEMA = "msw.hermes.wp-ai-integration-checkpoint.v1"
MAX_BODY = 128000
MAX_RESPONSE = 260000
ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,99}\Z")
STATUS = re.compile(r"[A-Z_]{2,40}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")


class SyncError(RuntimeError):
    """Only static, non-sensitive error codes are exposed."""


def canonical(value) -> bytes:
    def normalize(x):
        if isinstance(x, float):
            if not math.isfinite(x):
                raise SyncError("nonfinite_number")
            return int(x) if x.is_integer() else x
        if isinstance(x, dict):
            return {k: normalize(v) for k, v in x.items()}
        if isinstance(x, list):
            return [normalize(v) for v in x]
        return x
    try:
        return json.dumps(normalize(value), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise SyncError("json_contract") from None


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def strict_json(raw: bytes):
    def pairs(items):
        obj = {}
        for key, val in items:
            if key in obj:
                raise SyncError("duplicate_json_key")
            obj[key] = val
        return obj
    def constant(_):
        raise SyncError("nonfinite_number")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise SyncError("json_contract") from None


def timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value):
        raise SyncError("timestamp_invalid")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        raise SyncError("timestamp_invalid") from None


def text(value, limit, pattern=None):
    if (not isinstance(value, str) or len(value) > limit or value != value.strip()
            or any(ord(c) < 32 for c in value) or "<" in value or ">" in value
            or (pattern is not None and not pattern.fullmatch(value))):
        raise SyncError("text_contract")
    return value


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1000:
        raise SyncError("budget_contract")
    return value


def build_payload(doc, now=None):
    now = now or datetime.now(timezone.utc)
    if not isinstance(doc, dict) or doc.get("schema") != SOURCE_SCHEMA:
        raise SyncError("source_schema")
    ev, safe = doc.get("evidence"), doc.get("safety")
    if not isinstance(ev, dict) or not isinstance(safe, dict):
        raise SyncError("source_structure")
    reg, h = ev.get("registry"), ev.get("hermes")
    if not isinstance(reg, dict) or not isinstance(h, dict):
        raise SyncError("source_structure")
    revision, ack = reg.get("revision"), reg.get("lastAckRevision")
    if (type(revision) is not int or type(ack) is not int or not 0 <= ack <= revision <= 999999999):
        raise SyncError("revision_contract")
    seen = timestamp(h.get("lastSeen"))
    age = (now - seen).total_seconds()
    if age > 1800 or age < -300:
        raise SyncError("stale_source")
    if h.get("status") != "CONNECTED":
        raise SyncError("source_not_connected")
    providers, routes = doc.get("providerRoster"), doc.get("runtimeRoutes")
    if (not isinstance(providers, list) or not 1 <= len(providers) <= 200
            or not isinstance(routes, list) or len(routes) > 80):
        raise SyncError("record_bounds")
    if type(reg.get("total")) is not int or reg["total"] != len(providers):
        raise SyncError("registry_count_mismatch")
    p, r, pids, rids = [], [], set(), set()
    for item in providers:
        if not isinstance(item, dict):
            raise SyncError("provider_contract")
        ident = text(item.get("id"), 80, ID)
        if ident in pids:
            raise SyncError("duplicate_provider_id")
        pids.add(ident)
        if type(item.get("hold")) is not bool or type(item.get("keyReported")) is not bool:
            raise SyncError("boolean_contract")
        p.append({"id": ident, "group": text(item.get("group"), 80, ID),
                  "kind": text(item.get("kind"), 80, ID),
                  "registryStatus": text(item.get("registryStatus"), 40, STATUS),
                  "hold": item["hold"], "keyReported": item["keyReported"]})
    for item in routes:
        if not isinstance(item, dict):
            raise SyncError("route_contract")
        ident = text(item.get("id"), 100, ID)
        if ident in rids:
            raise SyncError("duplicate_route_id")
        rids.add(ident)
        last_test = item.get("lastTest") or ""
        if last_test:
            if timestamp(last_test) > now.replace(microsecond=0) + timedelta(seconds=300):
                raise SyncError("future_route_test")
        r.append({"id": ident, "provider": text(item.get("provider"), 80, ID),
                  "model": text(item.get("model"), 100), "tier": text(item.get("tier"), 30, ID),
                  "status": text(item.get("status"), 40, STATUS),
                  "reason": text(item.get("reason") or "", 100),
                  "lastTest": text(last_test, 40)})
    body = {"schema": SCHEMA, "registryRevision": revision, "lastAckRevision": ack,
            # Observation time belongs to the source, not this HTTP request.
            "observedAtUtc": seen.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "source": "hermes_staging_private_checkpoint",
            "hermes": {"status": "CONNECTED", "routerVersion": text(h.get("routerVersion"), 30),
                       "lastSeen": h["lastSeen"], "paidDailyLimitUsd": number(safe.get("paidDailyLimitUsd")),
                       "spentUsd": number(h.get("todayPaidSpentUsd")),
                       "reservedUsd": number(h.get("todayReservedUsd"))},
            "providers": p, "routes": r}
    if len(canonical(body)) > MAX_BODY:
        raise SyncError("payload_too_large")
    return body


def verify_snapshot(payload, result):
    if not isinstance(result, dict) or result.get("snapshotPresent") is not True:
        raise SyncError("snapshot_missing")
    snap = result.get("snapshot")
    if not isinstance(snap, dict):
        raise SyncError("snapshot_contract")
    server_digest = snap.get("digestSha256")
    if not isinstance(server_digest, str) or not SHA.fullmatch(server_digest):
        raise SyncError("server_digest_invalid")
    clean = {k: v for k, v in snap.items() if k != "digestSha256"}
    # This hash is independently computed over all received data, not echoed.
    expected, actual = digest(payload), digest(clean)
    if expected != actual:
        raise SyncError("independent_content_mismatch")
    return {"contentSha256": actual, "serverDigestSha256": server_digest,
            "registryRevision": payload["registryRevision"],
            "lastAckRevision": payload["lastAckRevision"],
            "sourceObservedAtUtc": payload["observedAtUtc"],
            "providerRecords": len(payload["providers"]), "runtimeRoutes": len(payload["routes"]),
            "inferenceTransportVerified": False}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, method, headers, body=None):
    if (url, method) not in {(SOURCE_URL, "GET"), (WP_URL, "GET"), (WP_URL, "POST")}:
        raise SyncError("destination_rejected")
    raw = None if body is None else canonical(body)
    if raw is not None and len(raw) > MAX_BODY:
        raise SyncError("payload_too_large")
    request = Request(url, data=raw, method=method, headers=headers)
    try:
        with build_opener(HTTPSHandler(), NoRedirect()).open(request, timeout=12) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise SyncError("response_too_large")
        result = strict_json(raw)
        if not isinstance(result, dict):
            raise SyncError("response_contract")
        return result
    except HTTPError as exc:
        raise SyncError("http_" + str(exc.code)) from None
    except (URLError, OSError, TimeoutError):
        raise SyncError("transport_unknown") from None


def read_source(env):
    token = env.get("MSW_HERMES_GITHUB_TOKEN", "").strip()
    if not token:
        raise SyncError("github_credential_missing")
    meta = request_json(SOURCE_URL, "GET", {"Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "msw-registry-cycle/" + VERSION})
    if meta.get("type") != "file" or not isinstance(meta.get("content"), str):
        raise SyncError("github_file_contract")
    try:
        raw = base64.b64decode(meta["content"], validate=False)
    except ValueError:
        raise SyncError("github_file_contract") from None
    if (len(raw) > MAX_BODY or meta.get("sha") !=
            hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()):
        raise SyncError("github_blob_integrity")
    return strict_json(raw)


def _database(root):
    if root.is_symlink() or root.parent.is_symlink():
        raise SyncError("storage_symlink")
    root.mkdir(parents=False, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    path = root / "sync.sqlite3"
    if path.is_symlink():
        raise SyncError("storage_symlink")
    db = sqlite3.connect(path, timeout=5, isolation_level=None)
    os.chmod(path, 0o600)
    db.execute("PRAGMA synchronous=FULL")
    db.execute("CREATE TABLE IF NOT EXISTS syncs(id TEXT PRIMARY KEY, payload BLOB NOT NULL, state TEXT NOT NULL, receipt TEXT, at REAL NOT NULL)")
    return db


def sync_once(doc, root, post, read, now=None):
    with closing(_database(root)) as db:
        # Reconcile any durable unknown write FIRST. This is a GET-only
        # action and must remain possible even when the upstream source has
        # become stale since the original POST. Never manufacture freshness.
        prior = db.execute("SELECT id,payload FROM syncs WHERE state='pending' ORDER BY at LIMIT 1").fetchone()
        if prior:
            previous = strict_json(prior[1])
            proof = verify_snapshot(previous, read())
            db.execute("UPDATE syncs SET state='verified',receipt=? WHERE id=? AND state='pending'", (canonical(proof).decode(), prior[0]))
            # Only after receipt readback may we validate a candidate for
            # a NEW POST. A stale source remains blocked and is never sent.
        payload = build_payload(doc, now)
        ident, encoded = digest(payload), canonical(payload)
        if prior and prior[0] == ident:
            return dict(proof, state="reconciled_without_post", version=VERSION)
        row = db.execute("SELECT state,receipt FROM syncs WHERE id=?", (ident,)).fetchone()
        if row:
            proof = verify_snapshot(payload, read())
            return dict(proof, state="unchanged_verified", version=VERSION)
        last = db.execute("SELECT payload FROM syncs WHERE state='verified' ORDER BY at DESC LIMIT 1").fetchone()
        if last:
            previous = strict_json(last[0])
            if (payload["registryRevision"] < previous["registryRevision"]
                    or payload["lastAckRevision"] < previous["lastAckRevision"]
                    or timestamp(payload["hermes"]["lastSeen"]) < timestamp(previous["hermes"]["lastSeen"])):
                raise SyncError("source_regression")
        # Durable admission precedes the one permitted POST.
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("INSERT INTO syncs VALUES(?,?,'pending',NULL,?)", (ident, encoded, time.time()))
            db.execute("COMMIT")
        except sqlite3.IntegrityError:
            db.execute("ROLLBACK")
            raise SyncError("concurrent_sync_claim") from None
        reply = post(payload)
        if (not isinstance(reply, dict) or reply.get("stored") is not True
                or reply.get("registryRevision") != payload["registryRevision"]
                or reply.get("lastAckRevision") != payload["lastAckRevision"]):
            raise SyncError("write_receipt_invalid")
        proof = verify_snapshot(payload, read())
        if reply.get("digestSha256") != proof["serverDigestSha256"]:
            raise SyncError("server_receipt_mismatch")
        db.execute("UPDATE syncs SET state='verified',receipt=? WHERE id=? AND state='pending'", (canonical(proof).decode(), ident))
        with closing(_database(root)) as fresh:
            state = fresh.execute("SELECT state,receipt FROM syncs WHERE id=?", (ident,)).fetchone()
            if state != ("verified", canonical(proof).decode()):
                raise SyncError("journal_readback_mismatch")
        # Bounded metadata retention; unresolved rows are never pruned.
        db.execute("DELETE FROM syncs WHERE state='verified' AND id NOT IN (SELECT id FROM syncs WHERE state='verified' ORDER BY at DESC LIMIT 128)")
        return dict(proof, state="verified_metadata_cycle", version=VERSION)


def run_live_cycle():
    env = os.environ
    if env.get("MSW_HERMES_WP_REGISTRY_CYCLE_ENABLED") != "1":
        return {"state": "disabled", "version": VERSION}
    if env.get("RENDER_SERVICE_ID") != SERVICE:
        return {"state": "wrong_service_blocked", "version": VERSION}
    if not os.path.ismount(MOUNT):
        return {"state": "persistent_disk_not_verified", "version": VERSION}
    token = env.get("MSW_WP_REGISTRY_SCOPED_TOKEN", "")
    if not SHA.fullmatch(token):
        return {"state": "scoped_credential_missing", "version": VERSION}
    try:
        if ROOT.is_symlink():
            raise SyncError("storage_symlink")
        ROOT.mkdir(mode=0o700, exist_ok=True)
        os.chmod(ROOT, 0o700)
        fd = os.open(ROOT / "cycle.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"state": "cycle_busy", "version": VERSION}
            doc = read_source(env)
            headers = {"X-MSW-Registry-Token": token, "Content-Type": "application/json",
                       "User-Agent": "msw-registry-cycle/" + VERSION}
            return sync_once(doc, ROOT,
                lambda payload: request_json(WP_URL, "POST", headers, payload),
                lambda: request_json(WP_URL, "GET", headers))
    except SyncError as exc:
        return {"state": "blocked_no_reissue", "code": str(exc), "version": VERSION}
    except Exception:
        return {"state": "uncertain_no_reissue", "version": VERSION}
