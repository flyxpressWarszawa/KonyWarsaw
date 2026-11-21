"""Bounded Python execution pilot, isolated from personal-chat and production queues.

Only two pure existing handlers are callable. A pinned manifest authorizes data;
no task can select a URL, repository, filesystem path or executable. Receipts
attest staging execution, never central Hermes ACK or ChatGPT delivery.
"""
from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

REPO = "flyxpressWarszawa/signal-harbor-companion"
ROOT = "knowledge/hermes-python-hub/staging"
REQUEST_PATH = ROOT + "/requests/python10-20261114.json"
SERVICE = "srv-dbnodkbrjlhs739drc5g"
DATA_ROOT = Path("/tmp/msw-aux-seo/python-task-pilot")
SCHEMA = "msw.python-task-pilot.v1"
IDS = {"py10-20261114-seo", "py10-20261114-rank"}
KINDS = {"seo.analyze", "research.rank"}
MAX_BYTES = 65536


class PilotError(RuntimeError):
    pass


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def result_path(task_id):
    if task_id not in IDS:
        raise PilotError("task_id_rejected")
    return ROOT + "/receipts/" + task_id + ".json"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubStore:
    def _call(self, method, path, body=None):
        allowed = {REQUEST_PATH} | {result_path(x) for x in IDS}
        if path not in allowed or method not in {"GET", "PUT"} or (method == "PUT" and path == REQUEST_PATH):
            raise PilotError("path_or_method_rejected")
        token = os.environ.get("MSW_HERMES_GITHUB_TOKEN", "").strip()
        if not token:
            raise PilotError("credential_missing")
        url = "https://api.github.com/repos/" + REPO + "/contents/" + path
        if method == "GET":
            url += "?ref=main"
        request = Request(url, data=None if body is None else wire(body), method=method, headers={
            "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
            "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "MSW-Python-Task-Pilot/1"})
        try:
            with build_opener(HTTPSHandler(), NoRedirect()).open(request, timeout=12) as response:
                raw = response.read(MAX_BYTES * 2 + 1)
            if len(raw) > MAX_BYTES * 2:
                raise PilotError("response_limit")
            return json.loads(raw)
        except HTTPError as exc:
            if method == "GET" and exc.code == 404:
                return None
            raise PilotError("github_http_" + str(exc.code)) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise PilotError("transport_uncertain") from None

    def get(self, path):
        meta = self._call("GET", path)
        if meta is None:
            return None
        if not isinstance(meta, dict) or meta.get("type") != "file" or not isinstance(meta.get("content"), str):
            raise PilotError("file_contract")
        raw = base64.b64decode(meta["content"], validate=False)
        if len(raw) > MAX_BYTES or meta.get("sha") != hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest():
            raise PilotError("file_integrity")
        return raw

    def create(self, path, raw):
        if len(raw) > MAX_BYTES:
            raise PilotError("receipt_limit")
        self._call("PUT", path, {"message": "Record isolated Python staging execution receipt",
                   "branch": "main", "content": base64.b64encode(raw).decode("ascii")})


def validate_manifest(raw, expected_sha):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES or not re.fullmatch(r"[a-f0-9]{64}", expected_sha or "") or sha(raw) != expected_sha:
        raise PilotError("manifest_digest")
    try:
        doc = json.loads(raw)
        if set(doc) != {"schema", "classification", "tasks"} or doc["schema"] != SCHEMA or doc["classification"] != "synthetic_public_test":
            raise ValueError()
        tasks = doc["tasks"]
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 2:
            raise ValueError()
        seen = set()
        for task in tasks:
            if (not isinstance(task, dict) or set(task) != {"task_id", "kind", "payload"}
                    or task["task_id"] not in IDS or task["task_id"] in seen
                    or task["kind"] not in KINDS or not isinstance(task["payload"], dict)
                    or len(wire(task["payload"])) > 10000):
                raise ValueError()
            seen.add(task["task_id"])
        return tasks
    except (ValueError, TypeError, KeyError):
        raise PilotError("manifest_contract") from None


def execute(task, root):
    from .tasks import seo_analyze, research_rank
    return {"seo.analyze": seo_analyze, "research.rank": research_rank}[task["kind"]](task["payload"], root)


def _db(root):
    if root.is_symlink():
        raise PilotError("storage_symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    path = root / "pilot.sqlite3"
    if path.is_symlink():
        raise PilotError("storage_symlink")
    db = sqlite3.connect(path, timeout=5, isolation_level=None)
    os.chmod(path, 0o600)
    db.execute("PRAGMA synchronous=FULL")
    db.execute("CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, request_sha TEXT NOT NULL, state TEXT NOT NULL, receipt BLOB)")
    return db


def run_batch(store, root, expected_sha, runner=execute, environment=None):
    env = os.environ if environment is None else environment
    tasks = validate_manifest(store.get(REQUEST_PATH), expected_sha)
    records = []
    for task in tasks:
        ident, digest = task["task_id"], sha(wire(task))
        path = result_path(ident)
        reused = False
        with closing(_db(root)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT request_sha,state,receipt FROM jobs WHERE id=?", (ident,)).fetchone()
            if row is None:
                db.execute("INSERT INTO jobs VALUES(?,?,'running',NULL)", (ident, digest))
            elif row[0] != digest:
                db.execute("ROLLBACK")
                raise PilotError("task_conflict")
            db.execute("COMMIT")
            if row is None:
                output = runner(task, root)
                receipt = {"schema": "msw.python-task-pilot.receipt.v1", "task_id": ident,
                    "kind": task["kind"], "request_sha256": digest, "manifest_sha256": expected_sha,
                    "output": output, "result_sha256": sha(wire(output)),
                    "executor_service": env.get("RENDER_SERVICE_ID", "local_test"),
                    "git_commit": env.get("RENDER_GIT_COMMIT", "local_test"),
                    "executed_at_utc": datetime.now(timezone.utc).isoformat(),
                    "production_mutation_performed": False, "provider_calls": 0,
                    "central_hermes_ack_verified": False, "chatgpt_delivery_verified": False}
                raw = wire(receipt)
                if len(raw) > MAX_BYTES:
                    raise PilotError("receipt_limit")
                db.execute("UPDATE jobs SET state='computed',receipt=? WHERE id=?", (raw, ident))
                state = "computed"
            else:
                state, raw = row[1], row[2]
                reused = True
                if state == "running" or raw is None:
                    raise PilotError("execution_uncertain_no_retry")
            try:
                saved = json.loads(raw)
                if (saved["task_id"] != ident or saved["request_sha256"] != digest
                        or saved["manifest_sha256"] != expected_sha
                        or saved["result_sha256"] != sha(wire(saved["output"]))
                        or saved["central_hermes_ack_verified"] is not False):
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                raise PilotError("local_receipt_integrity") from None
            with closing(_db(root)) as fresh:
                if fresh.execute("SELECT receipt FROM jobs WHERE id=?", (ident,)).fetchone() != (raw,):
                    raise PilotError("local_readback_mismatch")
            remote = store.get(path)
            if remote is not None:
                if remote != raw:
                    raise PilotError("remote_receipt_conflict")
            else:
                if state in {"publishing", "reported"}:
                    raise PilotError("publication_uncertain_no_retry")
                changed = db.execute("UPDATE jobs SET state='publishing' WHERE id=? AND state='computed'", (ident,)).rowcount
                if changed != 1:
                    raise PilotError("publisher_already_claimed")
                store.create(path, raw)
                if store.get(path) != raw:
                    raise PilotError("remote_readback_mismatch")
            db.execute("UPDATE jobs SET state='reported' WHERE id=?", (ident,))
            records.append({"task_id": ident, "state": "reported", "reused": reused,
                            "receipt_sha256": sha(raw), "readback_verified": True})
    return {"state": "verified_staging_python_results", "tasks": records,
            "central_hermes_ack_verified": False, "chatgpt_delivery_verified": False}


def run_live_once():
    if os.environ.get("MSW_HERMES_PYTHON_TASK_PILOT") != "1":
        return {"state": "disabled"}
    if os.environ.get("RENDER_SERVICE_ID") != SERVICE:
        return {"state": "wrong_service_blocked"}
    if not os.path.ismount(DATA_ROOT.parent):
        return {"state": "persistent_disk_not_verified"}
    try:
        return run_batch(GitHubStore(), DATA_ROOT, os.environ.get("MSW_HERMES_PYTHON_TASK_SHA256", ""))
    except PilotError as exc:
        return {"state": "blocked_no_retry", "code": str(exc)[:80]}
    except Exception:
        return {"state": "uncertain_no_retry"}
