"""Opt-in, sealed read-only snapshot of the existing MSW WordPress remote queue.

The *active* executor continues to require the native signed lease+ACK protocol.
GitHub is an archive, never a replacement for live claim, status or receipt checks.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .client import encode, signed_headers
from .recovery import protected_open

SCHEMA = "msw.hermes.queue-archive.v1"
STATES = ("pending", "running", "failed", "completed", "uncertain")
MAX_RECORDS = 12
MAX_BODY = 800_000
ROOT = "https://mobilny-serwis-warszawa.pl"


class MirrorError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _aes(key: bytes):
    if not isinstance(key, bytes) or len(key) != 32:
        raise MirrorError("archive_key_required")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as error:
        raise MirrorError("cryptography_optional_package_required") from error
    return AESGCM(key)


def archive_key(value: str) -> bytes:
    try:
        key = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise MirrorError("archive_key_invalid") from error
    _aes(key)
    return key


def check_record(record: dict, expected_state: str, snapshot_until: int) -> bytes:
    if not isinstance(record, dict) or set(record) != {"id", "kind", "payload", "payload_sha256", "job_key", "state", "attempts", "created_at", "updated_at"}:
        raise MirrorError("archive_record_fields")
    if not isinstance(record["id"], str) or re.fullmatch(r"[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}", record["id"]) is None:
        raise MirrorError("archive_record_id")
    if not isinstance(record["kind"], str) or not re.fullmatch(r"[a-z][a-z0-9.]{1,62}", record["kind"]):
        raise MirrorError("archive_record_kind")
    if record["state"] != expected_state:
        raise MirrorError("archive_record_state")
    for field in ("payload_sha256", "job_key"):
        if not isinstance(record[field], str) or re.fullmatch(r"[a-f0-9]{64}", record[field]) is None:
            raise MirrorError("archive_record_digest")
    if any(type(record[k]) is not int for k in ("attempts", "created_at", "updated_at")):
        raise MirrorError("archive_record_timestamps")
    if not 0 <= record["attempts"] < 65536 or not 0 < record["created_at"] <= snapshot_until or record["updated_at"] < record["created_at"]:
        raise MirrorError("archive_record_bounds")
    if not isinstance(record["payload"], str):
        raise MirrorError("archive_record_payload")
    raw = record["payload"].encode("utf-8")
    if len(raw) > 262144 or hashlib.sha256(raw).hexdigest() != record["payload_sha256"]:
        raise MirrorError("archive_payload_digest_mismatch")
    return raw


class Store:
    def __init__(self, directory: Path, key: bytes):
        self.directory = Path(directory) / "hermes-queue-mirror"
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.path = self.directory / "mirror.sqlite3"
        self.aes = _aes(key)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,kind TEXT NOT NULL,state TEXT NOT NULL,payload_sha256 TEXT NOT NULL,job_key TEXT NOT NULL,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,nonce BLOB NOT NULL,ciphertext BLOB NOT NULL,observed_at INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS cursors (state TEXT PRIMARY KEY,snapshot_until INTEGER NOT NULL,cursor_time INTEGER NOT NULL,cursor_id TEXT NOT NULL,finished INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS outbox (state TEXT PRIMARY KEY, envelope_json TEXT NOT NULL)")
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @staticmethod
    def aad(rec):
        return (rec["id"] + ":" + rec["kind"] + ":" + rec["payload_sha256"]).encode()

    def cursor(self, state, now):
        if state not in STATES:
            raise MirrorError("archive_state")
        with self.connect() as db:
            value = db.execute("SELECT snapshot_until,cursor_time,cursor_id,finished FROM cursors WHERE state=?", (state,)).fetchone()
        if value and value[3] and int(now) >= value[0] + 3600:
            # Next snapshot starts at the previous high-water mark. The one-
            # second overlap covers jobs at the same UNIX timestamp; IDs are
            # deduplicated in the encrypted ledger and private GitHub archive.
            return {"snapshot_until": int(now), "cursor_created_at": max(0, value[0] - 1), "cursor_id": "", "finished": False}
        return {"snapshot_until": value[0], "cursor_created_at": value[1], "cursor_id": value[2], "finished": bool(value[3])} if value else {"snapshot_until": int(now), "cursor_created_at": 0, "cursor_id": "", "finished": False}

    def pending_page(self, state):
        with self.connect() as db:
            row = db.execute("SELECT envelope_json FROM outbox WHERE state=?", (state,)).fetchone()
        if row is None:
            return None
        envelope = json.loads(row[0])
        if not isinstance(envelope, dict) or envelope.get("state") != state or not isinstance(envelope.get("sealed"), list):
            raise MirrorError("archive_outbox_integrity")
        return envelope

    def save_page(self, state: str, page: dict, cursor: dict, now: int):
        if state not in STATES or not isinstance(page, dict) or page.get("schema") != SCHEMA:
            raise MirrorError("archive_page_schema")
        if page.get("state") != state or type(page.get("snapshot_until")) is not int or page["snapshot_until"] != cursor["snapshot_until"]:
            raise MirrorError("archive_page_scope")
        if type(page.get("finished")) is not bool or not isinstance(page.get("records"), list) or len(page["records"]) > MAX_RECORDS:
            raise MirrorError("archive_page_bounds")
        rows = page["records"]
        previous = (cursor["cursor_created_at"], cursor["cursor_id"])
        seen = set()
        sealed = []
        for rec in rows:
            raw = check_record(rec, state, cursor["snapshot_until"])
            position = (rec["created_at"], rec["id"])
            if position <= previous or rec["id"] in seen:
                raise MirrorError("archive_page_order")
            previous = position
            seen.add(rec["id"])
            nonce = os.urandom(12)
            sealed_record = {k: rec[k] for k in ("id", "kind", "state", "payload_sha256", "job_key", "created_at", "updated_at")}
            # Reuse the original sealed bytes on retry. Otherwise a lost GitHub
            # acknowledgement could create a second blob for the same page.
            with self.connect() as db:
                existing = db.execute("SELECT kind,payload_sha256,nonce,ciphertext FROM jobs WHERE id=?", (rec["id"],)).fetchone()
            if existing is not None:
                if existing[0] != rec["kind"] or existing[1] != rec["payload_sha256"] or self.aes.decrypt(existing[2], existing[3], self.aad(rec)) != raw:
                    raise MirrorError("archive_existing_conflict")
                nonce, ciphertext = existing[2], existing[3]
            else:
                ciphertext = self.aes.encrypt(nonce, raw, self.aad(rec))
            sealed_record["nonce_b64"] = base64.b64encode(nonce).decode("ascii")
            sealed_record["ciphertext_b64"] = base64.b64encode(ciphertext).decode("ascii")
            sealed.append(sealed_record)
        declared = page.get("next_cursor")
        expected = {"created_at": previous[0], "id": previous[1]}
        if not isinstance(declared, dict) or declared != expected or (not rows and not page["finished"]):
            raise MirrorError("archive_page_cursor")
        # Local durable write and the sealed GitHub outbox form one transaction.
        # Do not fetch a changed origin page after an uncertain remote upload.
        envelope = {"state":state,"snapshot_until":page["snapshot_until"],
                    "next_cursor":page["next_cursor"],"finished":page["finished"],
                    "sealed":sealed}
        with self.connect() as db:
            old_page=db.execute("SELECT envelope_json FROM outbox WHERE state=?", (state,)).fetchone()
            if old_page is not None and json.loads(old_page[0]) != envelope:
                raise MirrorError("archive_outbox_pending_conflict")
            for rec, wire in zip(rows, sealed):
                old = db.execute("SELECT kind,payload_sha256,nonce,ciphertext FROM jobs WHERE id=?", (rec["id"],)).fetchone()
                if old:
                    if old[0] != rec["kind"] or old[1] != rec["payload_sha256"]:
                        raise MirrorError("archive_existing_conflict")
                    if self.aes.decrypt(old[2], old[3], self.aad(rec)) != rec["payload"].encode():
                        raise MirrorError("archive_existing_readback")
                else:
                    db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)", (rec["id"], rec["kind"], state, rec["payload_sha256"], rec["job_key"], rec["created_at"], rec["updated_at"], base64.b64decode(wire["nonce_b64"]), base64.b64decode(wire["ciphertext_b64"]), int(now)))
            if old_page is None:
                db.execute("INSERT INTO outbox VALUES(?,?)", (state, json.dumps(envelope, ensure_ascii=False, separators=(',',':'))))
        return sealed

    def commit_cursor(self, state: str, page: dict, now: int):
        # Only after the encrypted GitHub upload was verified (when enabled).
        next_cursor = page["next_cursor"]
        with self.connect() as db:
            db.execute("INSERT INTO cursors VALUES(?,?,?,?,?) ON CONFLICT(state) DO UPDATE SET snapshot_until=excluded.snapshot_until,cursor_time=excluded.cursor_time,cursor_id=excluded.cursor_id,finished=excluded.finished", (state, page["snapshot_until"], next_cursor["created_at"], next_cursor["id"], int(page["finished"])))
            db.execute("DELETE FROM outbox WHERE state=?", (state,))
        return self.cursor(state, now)

    def summary(self):
        with self.connect() as db:
            counts = dict(db.execute("SELECT state,COUNT(*) FROM jobs GROUP BY state").fetchall())
            cursor = dict((r[0], {"snapshot_until":r[1], "finished":bool(r[2])}) for r in db.execute("SELECT state,snapshot_until,finished FROM cursors").fetchall())
        return {"schema": SCHEMA, "encrypted_locally": True, "archive_only_no_execution": True, "counts": counts, "cursors": cursor}


class WordPressReader:
    def __init__(self, base: str, node: str, secret: str, opener=None):
        base = base.rstrip("/")
        if base != ROOT or not isinstance(node, str) or node != "msw-render-central-1" or not isinstance(secret, str) or len(secret) < 32:
            raise MirrorError("archive_authenticated_central_only")
        self.base, self.node, self.secret = base, node, secret
        self.opener = opener or build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))

    def fetch(self, state: str, cursor: dict):
        if state not in STATES:
            raise MirrorError("archive_state")
        payload = {"state": state, "snapshot_until": cursor["snapshot_until"], "cursor_created_at": cursor["cursor_created_at"], "cursor_id": cursor["cursor_id"], "limit": MAX_RECORDS}
        raw = encode(payload)
        headers = signed_headers("status", self.node, self.secret, raw)
        request = Request(self.base + "/wp-json/msw-hermes-queue/v1/export", data=raw, headers=headers, method="POST")
        try:
            with protected_open(self.opener, request, timeout=30) as response:
                body = response.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise MirrorError("archive_response_limit")
            return json.loads(body)
        except (HTTPError, URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            # Never include the HTTP response body or URLs containing secrets.
            raise MirrorError("archive_transport_unavailable") from error


class GitHubPrivateVault:
    """Optional encrypted-page write. Requires an EXPLICIT private repository.

    Never accepts a public repo, redirects, or a body containing raw job payloads.
    The owner supplies the scoped token through Render secrets (not GitHub code).
    """
    def __init__(self, repo: str, token: str, opener=None):
        if not isinstance(repo, str) or re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", repo) is None or not isinstance(token, str) or len(token) < 20:
            raise MirrorError("archive_private_github_configuration")
        self.repo, self.token = repo, token
        self.opener = opener or build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
        self.verified_at = 0.0
        self.default_branch = None

    def api(self, path: str, *, method="GET", data=None):
        headers={"Accept":"application/vnd.github+json", "Authorization":"Bearer "+self.token,
                 "X-GitHub-Api-Version":"2022-11-28", "User-Agent":"MSW-Hermes-Queue-Archive/1.0"}
        if data is not None:
            headers["Content-Type"]="application/json"
        request=Request("https://api.github.com/repos/"+self.repo+path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                raw=response.read(1_200_001)
            if len(raw)>1_200_000:
                raise MirrorError("archive_github_response_limit")
            return json.loads(raw)
        except HTTPError as error:
            if error.code == 404:
                return None
            raise MirrorError("archive_github_http_failure") from None
        except (URLError, TimeoutError, OSError, ValueError) as error:
            raise MirrorError("archive_github_unavailable") from error

    def require_private(self):
        if time.time()-self.verified_at<300:
            return
        info=self.api("")
        if not isinstance(info,dict) or info.get("private") is not True or info.get("archived") is True or info.get("permissions",{}).get("push") is not True:
            raise MirrorError("archive_destination_not_private_writable")
        branch = info.get("default_branch")
        if not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9._/-]{1,120}", branch):
            raise MirrorError("archive_github_default_branch")
        self.default_branch = branch
        self.verified_at=time.time()

    @staticmethod
    def git_blob_sha(data: bytes):
        return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\x00"+data).hexdigest()

    def put_page(self, state: str, page: dict, sealed: list):
        self.require_private()
        envelope = {"schema":SCHEMA,"state":state,"snapshot_until":page["snapshot_until"],"next_cursor":page["next_cursor"],"records":sealed,"encrypted":True,"execution_allowed":False}
        raw=encode(envelope)
        if len(raw)>990_000:
            raise MirrorError("archive_github_page_limit")
        digest=hashlib.sha256(raw).hexdigest()
        path="/contents/msw-hermes-queue/v1/"+state+"/"+str(page["snapshot_until"])+"/"+digest+".json"
        old=self.api(path)
        sha=self.git_blob_sha(raw)
        if old is not None:
            if not isinstance(old,dict) or old.get("sha")!=sha:
                raise MirrorError("archive_github_existing_conflict")
            return {"state":"existing_verified", "digest":digest}
        result=self.api(path, method="PUT", data=encode({"message":"Archive sealed MSW Hermes queue page", "content":base64.b64encode(raw).decode(), "branch":self.default_branch}))
        if not isinstance(result,dict) or result.get("content",{}).get("sha")!=sha:
            raise MirrorError("archive_github_write_unverified")
        readback=self.api(path)
        if not isinstance(readback,dict) or readback.get("sha")!=sha:
            raise MirrorError("archive_github_readback_unverified")
        return {"state":"uploaded_verified", "digest":digest}


class Mirror:
    def __init__(self, store: Store, reader: WordPressReader, vault=None, clock=time.time):
        self.store,self.reader,self.vault,self.clock=store,reader,vault,clock

    def step(self, state="pending"):
        if self.vault is None:
            raise MirrorError("archive_private_github_not_configured")
        now=int(self.clock())
        cursor=self.store.cursor(state, now)
        if cursor["finished"]:
            return {"state":"snapshot_complete", "mirror":self.store.summary()}
        waiting=self.store.pending_page(state)
        if waiting is None:
            page=self.reader.fetch(state,cursor)
            sealed=self.store.save_page(state,page,cursor,now)
        else:
            page={"schema":SCHEMA,"state":waiting["state"],
                  "snapshot_until":waiting["snapshot_until"],
                  "next_cursor":waiting["next_cursor"],
                  "finished":waiting["finished"]}
            sealed=waiting["sealed"]
        remote=None
        if sealed:
            remote=self.vault.put_page(state,page,sealed)
        self.store.commit_cursor(state,page,now)
        return {"state":"page_archived", "kind":state,"records_archived":len(sealed),"private_github_readback":remote["state"] if remote else "empty_page", "mirror":self.store.summary()}


def from_environment(directory):
    if os.environ.get("MSW_HERMES_QUEUE_MIRROR", "0") != "1":
        raise MirrorError("archive_opt_in_required")
    key=archive_key(os.environ.get("MSW_HERMES_QUEUE_MIRROR_KEY_B64", ""))
    store=Store(Path(directory),key)
    reader=WordPressReader(os.environ.get("MSW_WP_URL", ""),os.environ.get("MSW_NODE",""),os.environ.get("MSW_SECRET",""))
    repo=os.environ.get("MSW_HERMES_GITHUB_QUEUE_REPO", "")
    token=os.environ.get("MSW_HERMES_GITHUB_QUEUE_TOKEN", "")
    vault=GitHubPrivateVault(repo,token) if repo or token else None
    if bool(repo)!=bool(token):
        raise MirrorError("archive_github_incomplete_configuration")
    if vault is None:
        raise MirrorError("archive_private_github_not_configured")
    return Mirror(store,reader,vault)


class MirrorLane:
    """A single deliberately slow archive thread, off by default in runner.

    This copies metadata+encrypted payload, NOT job ownership or execution.
    The signed WordPress queue remains the sole authority for claim/ACK.
    """
    def __init__(self, directory):
        self.mirror = from_environment(directory)

    def start(self, stopping):
        import threading
        def run():
            while not stopping():
                delay = 900  # Default to 15-minute cooldown on any uncertainty.
                try:
                    result = self.mirror.step("pending")
                    # Never log identifiers, payloads or unencrypted row contents.
                    print(json.dumps({"hermes_queue_archive": {
                        "state": result["state"],
                        "records_archived": result.get("records_archived", 0),
                        "github_receipt": result.get("private_github_readback", "none"),
                    }}), flush=True)
                    if result["state"] == "page_archived":
                        delay = 180  # <=12 archived rows every three minutes.
                    elif result["state"] == "snapshot_complete":
                        delay = 3600
                except Exception as error:
                    print(json.dumps({"hermes_queue_archive": {
                        "state": "retry_deferred", "error_class": type(error).__name__,
                        "code": str(error) if isinstance(error, MirrorError) else "unclassified",
                    }}), flush=True)
                deadline=time.monotonic()+delay
                while not stopping() and time.monotonic()<deadline:
                    time.sleep(min(1, max(0, deadline-time.monotonic())))
        thread=threading.Thread(target=run, name="msw-hermes-queue-mirror", daemon=True)
        thread.start()
        return thread
