#!/usr/bin/env python3
"""Transport one public MSW coding request; never execute task content.

Only the fixed main-branch workflow may obtain credentials. Tokens stay in
memory, queue files are immutable, and delivery needs independent WordPress ACK.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import ssl
import time
from urllib import error, parse, request

REPOSITORY = "flyxpressWarszawa/KonyWarsaw"
QUEUE_BRANCH = "msw/coding-queue"
WORKFLOW_REF = REPOSITORY + "/.github/workflows/msw-coding-bridge.yml@refs/heads/main"
AUDIENCE = "https://mobilny-serwis-warszawa.pl/msw-github-coding"
EXCHANGE_URL = "https://mobilny-serwis-warszawa.pl/wp-json/msw-github-coding/v1/exchange"
API_ROOT = "https://api.github.com/repos/" + REPOSITORY
API_VERSION = "2026-03-10"
TASK_SCHEMA = "msw.coding-task.v1"
EXCHANGE_SCHEMA = "msw.coding-exchange.v1"
SUPPORT = {
    "technical-core": "github_agent_scope", "portal": "github_agent_scope",
    "compute-adapters": "github_agent_scope", "research": "github_agent_scope",
    "runtime": "chatgpt_branch_worker", "owned-llm": "chatgpt_branch_worker",
    "wordpress": "source_sync_required",
}
TARGETS = set(SUPPORT)
TASK_KEYS = {"schema", "id", "request_id", "source_engine", "target", "title", "objective",
             "acceptance", "public_data", "created_at", "request_sha256"}
ACK_FIELDS = ("task_id", "request_sha256", "commit_sha", "task_path", "repository", "queue_branch")
MAX_TASK_BYTES = 16384
MAX_RESPONSE_BYTES = 131072
HTTP_TIMEOUT = 15
SHA = re.compile(r"[a-f0-9]{40}\Z")
HEX32 = re.compile(r"[a-f0-9]{32}\Z")
HEX64 = re.compile(r"[a-f0-9]{64}\Z")
SECRET_RE = re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{35,}|AKIA[A-Z0-9]{16})\b")


class BridgeError(Exception):
    """Fixed public codes only: never embed a URL, token or response body."""

    def __init__(self, code, *, stage="validation", status=None):
        super().__init__(code)
        self.code, self.stage, self.status = code, stage, status


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BridgeError("duplicate_json_key")
        result[key] = value
    return result


def decode_json(raw):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(BridgeError("nonfinite_json")))
    except (UnicodeError, ValueError, TypeError):
        raise BridgeError("invalid_json") from None


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise BridgeError("invalid_json") from None


def utc_timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise BridgeError("invalid_timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise BridgeError("invalid_timestamp") from None


def validate_task(task):
    if not isinstance(task, dict) or set(task) != TASK_KEYS or task.get("schema") != TASK_SCHEMA:
        raise BridgeError("invalid_task_schema")
    if task["public_data"] is not True:
        raise BridgeError("task_not_public")
    for field, maximum in (("title", 120), ("objective", 4000)):
        if not isinstance(task[field], str) or not 1 <= len(task[field]) <= maximum or not task[field].strip():
            raise BridgeError("invalid_task_text")
    if not isinstance(task["source_engine"], str) or not re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", task["source_engine"]):
        raise BridgeError("invalid_source_engine")
    if not isinstance(task["request_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", task["request_id"]):
        raise BridgeError("invalid_request_id")
    if not isinstance(task["target"], str) or task["target"] not in TARGETS:
        raise BridgeError("invalid_target")
    criteria = task["acceptance"]
    if (not isinstance(criteria, list) or not 1 <= len(criteria) <= 8
            or any(not isinstance(x, str) or not 1 <= len(x) <= 400 or not x.strip() for x in criteria)):
        raise BridgeError("invalid_acceptance")
    utc_timestamp(task["created_at"])
    identity = hashlib.sha256((task["source_engine"] + ":" + task["request_id"]).encode()).hexdigest()[:32]
    if not isinstance(task["id"], str) or not HEX32.fullmatch(task["id"]) or task["id"] != identity:
        raise BridgeError("task_id_mismatch")
    unsigned = {key: value for key, value in task.items() if key != "request_sha256"}
    digest = task["request_sha256"]
    if (not isinstance(digest, str) or not HEX64.fullmatch(digest)
            or not hmac.compare_digest(digest, hashlib.sha256(canonical(unsigned)).hexdigest())):
        raise BridgeError("task_digest_mismatch")
    encoded = canonical(task) + b"\n"
    if len(encoded) > MAX_TASK_BYTES:
        raise BridgeError("task_too_large")
    if SECRET_RE.search(encoded.decode("utf-8")):
        raise BridgeError("secret_like_task_content")
    return encoded


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class JsonHTTP:
    def __init__(self):
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.opener = request.build_opener(request.ProxyHandler({}), NoRedirect(), request.HTTPSHandler(context=context))

    def request(self, method, url, token, payload=None, *, stage, allowed=(200,), max_bytes=MAX_RESPONSE_BYTES):
        try:
            parts = parse.urlsplit(url)
            valid = (parts.scheme == "https" and bool(parts.hostname) and not parts.username
                     and not parts.password and not parts.fragment and parts.port in (None, 443))
        except ValueError:
            valid = False
        if not valid:
            raise BridgeError("invalid_transport_url", stage=stage)
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json",
                   "User-Agent": "MSW-Coding-Bridge/1", "Content-Type": "application/json"}
        if parts.hostname == "api.github.com":
            headers.update(Accept="application/vnd.github+json", **{"X-GitHub-Api-Version": API_VERSION})
        req = request.Request(url, data=canonical(payload) if payload is not None else None, headers=headers, method=method)
        try:
            response = self.opener.open(req, timeout=HTTP_TIMEOUT)
        except error.HTTPError as exc:
            status = exc.code
            exc.close()
            if status in allowed:
                return status, None
            raise BridgeError("redirect_blocked" if 300 <= status < 400 else "http_error", stage=stage, status=status) from None
        except (error.URLError, OSError, ValueError):
            raise BridgeError("network_error", stage=stage) from None
        try:
            with response:
                status = response.status
                if status not in allowed:
                    raise BridgeError("http_error", stage=stage, status=status)
                length = response.headers.get("Content-Length")
                if length and (not length.isdecimal() or int(length) > max_bytes):
                    raise BridgeError("response_too_large", stage=stage)
                raw = response.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise BridgeError("response_too_large", stage=stage)
        except (OSError, ValueError):
            raise BridgeError("network_error", stage=stage) from None
        try:
            return status, decode_json(raw)
        except BridgeError as exc:
            raise BridgeError(exc.code, stage=stage) from None


class BridgeClient:
    def __init__(self, environ=None, http=None):
        self.env = os.environ if environ is None else environ
        self.http = JsonHTTP() if http is None else http
        self.receipt = {"schema": "msw.coding-bridge.receipt.v1", "state": "not_started",
                        "repository": REPOSITORY, "queue_branch": QUEUE_BRANCH,
                        "code_executed": False, "production_changed": False}

    def credentials(self):
        context = {"GITHUB_REPOSITORY": REPOSITORY, "GITHUB_REF": "refs/heads/main", "GITHUB_WORKFLOW_REF": WORKFLOW_REF}
        if any(self.env.get(key) != value for key, value in context.items()):
            raise BridgeError("untrusted_workflow_context", stage="credentials")
        if not re.fullmatch(r"[1-9][0-9]{0,19}", self.env.get("GITHUB_RUN_ID", "")):
            raise BridgeError("invalid_run_context", stage="credentials")
        for key in ("GITHUB_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL"):
            if not isinstance(self.env.get(key), str) or not self.env[key]:
                raise BridgeError("missing_workflow_credentials", stage="credentials")
        try:
            parts = parse.urlsplit(self.env["ACTIONS_ID_TOKEN_REQUEST_URL"])
            valid = (parts.scheme == "https" and (parts.hostname or "").endswith(".actions.githubusercontent.com")
                     and not parts.username and not parts.password and not parts.fragment and parts.port in (None, 443))
        except ValueError:
            valid = False
        if not valid:
            raise BridgeError("invalid_oidc_url", stage="credentials")
        query = [(key, value) for key, value in parse.parse_qsl(parts.query, keep_blank_values=True) if key != "audience"]
        query.append(("audience", AUDIENCE))
        url = parse.urlunsplit(parts._replace(query=parse.urlencode(query)))
        _, value = self.http.request("GET", url, self.env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"], stage="oidc", max_bytes=32768)
        token = value.get("value") if isinstance(value, dict) else None
        if (not isinstance(token, str) or not 64 <= len(token) <= 24576
                or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token)):
            raise BridgeError("invalid_oidc_response", stage="oidc")
        self.oidc_token, self.github_token = token, self.env["GITHUB_TOKEN"]
        self.receipt["run_id"] = self.env["GITHUB_RUN_ID"]

    def exchange(self, operation, **values):
        _, result = self.http.request("POST", EXCHANGE_URL, self.oidc_token,
                                      {"operation": operation, **values}, stage="exchange_" + operation)
        if not isinstance(result, dict) or result.get("schema") != EXCHANGE_SCHEMA or result.get("ok") is not True:
            raise BridgeError("invalid_exchange_response", stage="exchange_" + operation)
        if result.get("run_id") != self.env["GITHUB_RUN_ID"]:
            raise BridgeError("exchange_run_mismatch", stage="exchange_" + operation)
        return result

    def branch_head(self):
        _, value = self.http.request("GET", API_ROOT + "/git/ref/heads/" + QUEUE_BRANCH,
                                     self.github_token, stage="queue_branch")
        obj = value.get("object", {}) if isinstance(value, dict) else {}
        sha = obj.get("sha")
        if obj.get("type") != "commit" or not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise BridgeError("invalid_queue_branch", stage="queue_branch")
        return sha

    def read_file(self, task_path, commit):
        url = API_ROOT + "/contents/" + task_path + "?" + parse.urlencode({"ref": commit})
        status, value = self.http.request("GET", url, self.github_token, stage="queue_read", allowed=(200, 404))
        if status == 404:
            return None
        if (not isinstance(value, dict) or value.get("type") != "file" or value.get("path") != task_path
                or value.get("encoding") != "base64" or not isinstance(value.get("content"), str)
                or type(value.get("size")) is not int or not 0 <= value["size"] <= MAX_TASK_BYTES):
            raise BridgeError("invalid_queue_file", stage="queue_read")
        try:
            data = base64.b64decode("".join(value["content"].split()), validate=True)
        except (ValueError, UnicodeError):
            raise BridgeError("invalid_queue_encoding", stage="queue_read") from None
        if len(data) != value["size"] or len(data) > MAX_TASK_BYTES:
            raise BridgeError("invalid_queue_size", stage="queue_read")
        return data

    @staticmethod
    def require_identical(actual, expected):
        if actual is None or not hmac.compare_digest(hashlib.sha256(actual).digest(), hashlib.sha256(expected).digest()):
            raise BridgeError("immutable_task_conflict", stage="queue_write")

    def store(self, task, encoded):
        task_path = "msw-coding-tasks/" + task["id"] + ".json"
        head = self.branch_head()
        existing = self.read_file(task_path, head)
        if existing is not None:
            self.require_identical(existing, encoded)
            return head, task_path, False
        payload = {"message": "Record MSW coding task " + task["id"], "branch": QUEUE_BRANCH,
                   "content": base64.b64encode(encoded).decode("ascii")}
        # A timeout may occur after the server committed. Preserve uncertainty.
        self.receipt["state"] = "write_unconfirmed"
        try:
            _, value = self.http.request("PUT", API_ROOT + "/contents/" + task_path,
                                         self.github_token, payload, stage="queue_write", allowed=(201,))
        except BridgeError as exc:
            if exc.status not in (409, 422):
                if exc.status is not None and 400 <= exc.status < 500 and exc.status != 408:
                    self.receipt["state"] = "blocked"
                raise
            # A concurrent writer may have won. No overwrite and no second PUT.
            head = self.branch_head()
            self.require_identical(self.read_file(task_path, head), encoded)
            return head, task_path, False
        commit = value.get("commit", {}).get("sha") if isinstance(value, dict) else None
        if not isinstance(commit, str) or not SHA.fullmatch(commit):
            raise BridgeError("invalid_commit_receipt", stage="queue_write")
        self.require_identical(self.read_file(task_path, commit), encoded)
        return commit, task_path, True

    def run(self, operation):
        if operation not in ("probe", "deliver"):
            raise BridgeError("invalid_operation")
        self.credentials()
        probe = self.exchange("probe")
        self.receipt["operation"] = operation
        if probe.get("state") == "held":
            self.receipt["state"] = "held"
            return self.receipt
        if probe.get("state") != "connected":
            raise BridgeError("probe_not_connected", stage="exchange_probe")
        self.receipt["state"] = "connected"
        if operation == "probe":
            return self.receipt
        claimed = self.exchange("claim")
        if claimed.get("state") in ("idle", "held"):
            self.receipt["state"] = claimed["state"]
            return self.receipt
        if claimed.get("state") != "leased":
            raise BridgeError("invalid_claim_state", stage="exchange_claim")
        task = claimed.get("task")
        encoded = validate_task(task)
        lease = claimed.get("lease_token")
        if not isinstance(lease, str) or not HEX64.fullmatch(lease):
            raise BridgeError("invalid_lease", stage="exchange_claim")
        if utc_timestamp(claimed.get("lease_expires_at")) <= datetime.now(timezone.utc):
            raise BridgeError("expired_lease", stage="exchange_claim")
        support = SUPPORT[task["target"]]
        if claimed.get("execution_support") != support:
            raise BridgeError("invalid_execution_support", stage="exchange_claim")
        self.receipt.update(task_id=task["id"], request_sha256=task["request_sha256"], execution_support=support)
        commit, task_path, created = self.store(task, encoded)
        self.receipt.update(state="stored_unacknowledged", commit_sha=commit, task_path=task_path, created=created,
                            task_url="https://github.com/" + REPOSITORY + "/blob/" + commit + "/" + task_path)
        ack = {"task_id": task["id"], "request_sha256": task["request_sha256"], "lease_token": lease,
               "commit_sha": commit, "task_path": task_path, "repository": REPOSITORY, "queue_branch": QUEUE_BRANCH}
        for attempt in range(2):
            try:
                confirmed = self.exchange("ack", **ack)
                break
            except BridgeError as exc:
                if attempt or not (exc.status == 503 or exc.code == "network_error"):
                    raise
                time.sleep(1)
        if confirmed.get("state") != "delivered" or any(confirmed.get(key) != ack[key] for key in ACK_FIELDS):
            raise BridgeError("delivery_not_confirmed", stage="exchange_ack")
        self.receipt["state"] = "delivered"
        return self.receipt


def write_public_receipt(receipt, environ):
    encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    destination = environ.get("MSW_BRIDGE_RECEIPT_PATH")
    if destination:
        Path(destination).write_text(encoded, encoding="utf-8")
    summary = environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        lines = ["## MSW coding bridge", "", "- Transport result: **" + receipt["state"] + "**.",
                 "- Queue branch: `" + QUEUE_BRANCH + "`.",
                 "- This transports task data; it does not execute task code or deploy an application."]
        if receipt.get("task_url"):
            lines.append("- Immutable task: [" + receipt["task_id"] + "](" + receipt["task_url"] + ").")
        if receipt.get("error"):
            lines.append("- Failure: `" + receipt["error"] + "` at `" + receipt["stage"] + "`.")
        with open(summary, "a", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
    print(encoded, end="")


def main(argv=None, environ=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("probe", "deliver"))
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    client = BridgeClient(env)
    code = 0
    try:
        client.run(args.operation)
    except BridgeError as exc:
        if client.receipt["state"] not in ("write_unconfirmed", "stored_unacknowledged"):
            client.receipt["state"] = "blocked"
        client.receipt.update(error=exc.code, stage=exc.stage)
        if exc.status is not None:
            client.receipt["http_status"] = exc.status
        code = 1
    except Exception:
        if client.receipt["state"] not in ("write_unconfirmed", "stored_unacknowledged"):
            client.receipt["state"] = "blocked"
        client.receipt.update(error="unexpected_client_error", stage="client")
        code = 1
    write_public_receipt(client.receipt, env)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
