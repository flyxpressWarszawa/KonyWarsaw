#!/usr/bin/env python3
"""Prepare verified public task data for a prompt; never grant or execute work.

Only fixed GitHub GET endpoints are used. Queue data cannot select a URL, path,
branch, policy or command. Import this module and its dependencies from the
trusted base checkout, never from the queue or a candidate worktree.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parents[1]


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Reuse the transport's one canonical task validator, not a second schema.
bridge = _module("msw_task_context_bridge", ROOT / "msw-coding-bridge/client.py")
controller = _module("msw_task_context_controller", Path(__file__).with_name("controller.py"))

SCHEMA = "msw.coding-task-context.v1"
MAIN_SCOPES = {
    "technical-core": ["msw-ai-technical-core/"],
    "portal": ["msw-ai-centrum-robotow/app/", "msw-ai-centrum-robotow/tests/"],
    "compute-adapters": ["msw-compute-node-adapters/"],
    "research": ["msw-research-engine/"],
}
TRUE_FLAGS = ("task_identity_verified", "queue_membership_verified", "production_hold")
FALSE_FLAGS = ("execution_authorized", "code_executed", "tests_verified", "pr_verified", "deployed_verified")
CONTEXT_KEYS = {
    "schema", "status", "repository", "queue_branch", "queue_commit", "queue_head_sha",
    "task_path", "task_id", "request_sha256", "file_sha256", "task", "target",
    "base_branch", "base_sha", "allowed_paths", "verified_at", *TRUE_FLAGS, *FALSE_FLAGS,
}


class Blocked(ValueError):
    """A fixed public error code; never include tokens or remote response text."""

    def __init__(self, code, *, stage="validation", http_status=None):
        super().__init__(code)
        self.code, self.stage = code, stage
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None


def _match(pattern, value, code):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise Blocked(code)
    return value


def _task(task):
    try:
        return bridge.validate_task(task)
    except bridge.BridgeError as exc:
        raise Blocked(exc.code, stage="task") from None


def _json(raw, *, stage):
    try:
        return bridge.decode_json(raw)
    except bridge.BridgeError as exc:
        raise Blocked(exc.code, stage=stage) from None
    except (RecursionError, OverflowError):
        raise Blocked("invalid_json", stage=stage) from None


def allowed_paths(target, *, repository_root=None):
    """Resolve the registered target and require the existing main-only policy."""
    if not isinstance(target, str) or target not in MAIN_SCOPES:
        raise Blocked("unsupported_executor_target", stage="scope")
    root = ROOT if repository_root is None else Path(repository_root)
    try:
        path = root / "msw-coding-bridge/targets.json"
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 32768:
            raise Blocked("invalid_target_catalog", stage="scope")
        catalog = _json(path.read_bytes(), stage="scope")
        policy = controller.load_policy(root / "msw-github-engine/policy.json")
    except (OSError, controller.Blocked):
        raise Blocked("invalid_target_policy", stage="scope") from None
    if (not isinstance(catalog, dict) or catalog.get("schema") != "msw.coding-targets.v1"
            or catalog.get("repository") != bridge.REPOSITORY
            or catalog.get("queue_branch") != bridge.QUEUE_BRANCH
            or catalog.get("task_directory") != "msw-coding-tasks"
            or not isinstance(catalog.get("targets"), dict)):
        raise Blocked("invalid_target_catalog", stage="scope")
    entry = catalog["targets"].get(target)
    if (not isinstance(entry, dict) or entry.get("branch") != "main"
            or entry.get("execution_support") != "github_agent_scope"
            or entry.get("paths") != MAIN_SCOPES[target]
            or any(path not in policy["allowed_prefixes"] for path in entry["paths"])):
        raise Blocked("target_scope_mismatch", stage="scope")
    return list(entry["paths"])


def validate_context(context, *, repository_root=None):
    """Check an immutable context's data and scope, without asserting freshness.

    This checks the recorded observation, not today's queue state or admission.
    An external digest pin must bind this object to the trusted preparation step.
    """
    if (not isinstance(context, dict) or set(context) != CONTEXT_KEYS
            or context.get("schema") != SCHEMA or context.get("status") != "CONTEXT_READY"):
        raise Blocked("invalid_context_schema")
    if (context["repository"] != bridge.REPOSITORY or context["queue_branch"] != bridge.QUEUE_BRANCH
            or context["base_branch"] != "main"):
        raise Blocked("context_destination_mismatch")
    for field in ("queue_commit", "queue_head_sha", "base_sha"):
        _match(bridge.SHA, context[field], "invalid_" + field)
    _match(bridge.HEX32, context["task_id"], "invalid_task_id")
    for field in ("request_sha256", "file_sha256"):
        _match(bridge.HEX64, context[field], "invalid_" + field)
    if (any(context[field] is not True for field in TRUE_FLAGS)
            or any(context[field] is not False for field in FALSE_FLAGS)):
        raise Blocked("invalid_context_claims")
    try:
        bridge.utc_timestamp(context["verified_at"])
    except bridge.BridgeError:
        raise Blocked("invalid_verified_at") from None
    raw = _task(context["task"])
    task = context["task"]
    if (context["task_id"] != task["id"] or context["request_sha256"] != task["request_sha256"]
            or context["target"] != task["target"]
            or context["task_path"] != "msw-coding-tasks/" + task["id"] + ".json"
            or not hmac.compare_digest(context["file_sha256"], hashlib.sha256(raw).hexdigest())):
        raise Blocked("context_task_mismatch")
    if context["allowed_paths"] != allowed_paths(task["target"], repository_root=repository_root):
        raise Blocked("context_scope_mismatch")
    return context


def context_bytes(context, *, repository_root=None):
    """Exact public artifact bytes: canonical UTF-8 JSON followed by one LF."""
    validate_context(context, repository_root=repository_root)
    return bridge.canonical(context) + b"\n"


class QueueReader:
    """Bounded GitHub reads; no API operation is selected by task text."""

    def __init__(self, token, http=None):
        if (not isinstance(token, str) or not 1 <= len(token) <= 16384
                or any(ord(char) <= 32 or ord(char) >= 127 for char in token)):
            raise Blocked("missing_or_invalid_read_token", stage="credentials")
        self._token = token
        self._http = bridge.JsonHTTP() if http is None else http

    def _get(self, suffix, stage):
        try:
            status, value = self._http.request("GET", bridge.API_ROOT + suffix, self._token,
                                               stage=stage, max_bytes=bridge.MAX_RESPONSE_BYTES)
        except bridge.BridgeError as exc:
            raise Blocked("github_read_failed", stage=stage, http_status=exc.status) from None
        if status != 200 or not isinstance(value, dict):
            raise Blocked("invalid_github_response", stage=stage)
        return value

    def _entry(self, tree_sha, name, *, mode, kind):
        tree = self._get("/git/trees/" + tree_sha, "queue_tree")
        if (tree.get("sha") != tree_sha or tree.get("truncated") is not False
                or not isinstance(tree.get("tree"), list)):
            raise Blocked("invalid_queue_tree", stage="queue_tree")
        matches = [item for item in tree["tree"] if isinstance(item, dict) and item.get("path") == name]
        if len(matches) != 1:
            raise Blocked("missing_or_ambiguous_task_path", stage="queue_tree")
        entry = matches[0]
        if (entry.get("mode") != mode or entry.get("type") != kind
                or not isinstance(entry.get("sha"), str) or not bridge.SHA.fullmatch(entry["sha"])):
            raise Blocked("invalid_task_path_type", stage="queue_tree")
        return entry

    def read_task(self, task_id, queue_commit):
        # These checks also protect callers that use QueueReader directly.
        _match(bridge.HEX32, task_id, "invalid_task_id")
        _match(bridge.SHA, queue_commit, "invalid_queue_commit")
        commit = self._get("/git/commits/" + queue_commit, "queue_commit")
        tree = commit.get("tree")
        if (commit.get("sha") != queue_commit or not isinstance(tree, dict)
                or not isinstance(tree.get("sha"), str) or not bridge.SHA.fullmatch(tree["sha"])):
            raise Blocked("invalid_queue_commit_response", stage="queue_commit")
        directory = self._entry(tree["sha"], "msw-coding-tasks", mode="040000", kind="tree")
        entry = self._entry(directory["sha"], task_id + ".json", mode="100644", kind="blob")
        if type(entry.get("size")) is not int or not 1 <= entry["size"] <= bridge.MAX_TASK_BYTES:
            raise Blocked("invalid_task_size", stage="queue_tree")
        blob = self._get("/git/blobs/" + entry["sha"], "queue_blob")
        if (blob.get("sha") != entry["sha"] or blob.get("encoding") != "base64"
                or type(blob.get("size")) is not int or blob["size"] != entry["size"]
                or not isinstance(blob.get("content"), str)
                or len(blob["content"]) > bridge.MAX_TASK_BYTES * 2):
            raise Blocked("invalid_queue_blob", stage="queue_blob")
        try:
            raw = base64.b64decode(blob["content"].replace("\r", "").replace("\n", ""), validate=True)
        except (ValueError, UnicodeError):
            raise Blocked("invalid_queue_encoding", stage="queue_blob") from None
        digest = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
        if len(raw) != entry["size"] or not hmac.compare_digest(digest, entry["sha"]):
            raise Blocked("queue_blob_identity_mismatch", stage="queue_blob")
        return raw

    def membership(self, queue_commit):
        _match(bridge.SHA, queue_commit, "invalid_queue_commit")
        value = self._get("/git/ref/heads/" + bridge.QUEUE_BRANCH, "queue_membership")
        obj = value.get("object")
        if (value.get("ref") != "refs/heads/" + bridge.QUEUE_BRANCH or not isinstance(obj, dict)
                or obj.get("type") != "commit" or not isinstance(obj.get("sha"), str)
                or not bridge.SHA.fullmatch(obj["sha"])):
            raise Blocked("invalid_queue_ref", stage="queue_membership")
        head = obj["sha"]
        if head != queue_commit:
            comparison = self._get("/compare/" + queue_commit + "..." + head + "?per_page=1", "queue_membership")
            base, merge_base = comparison.get("base_commit"), comparison.get("merge_base_commit")
            if (comparison.get("status") != "ahead" or type(comparison.get("ahead_by")) is not int
                    or comparison["ahead_by"] < 1 or type(comparison.get("behind_by")) is not int
                    or comparison["behind_by"] != 0 or not isinstance(base, dict)
                    or base.get("sha") != queue_commit or not isinstance(merge_base, dict)
                    or merge_base.get("sha") != queue_commit):
                raise Blocked("commit_not_in_queue", stage="queue_membership")
        return head


def prepare_context(task_id, queue_commit, file_sha256, base_sha, *, token, http=None, repository_root=None):
    """Read one pinned task and capture queue membership; return no execution grant."""
    _match(bridge.HEX32, task_id, "invalid_task_id")
    _match(bridge.SHA, queue_commit, "invalid_queue_commit")
    _match(bridge.HEX64, file_sha256, "invalid_file_sha256")
    _match(bridge.SHA, base_sha, "invalid_base_sha")
    reader = QueueReader(token, http=http)
    raw = reader.read_task(task_id, queue_commit)
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), file_sha256):
        raise Blocked("task_file_digest_mismatch", stage="task")
    task = _json(raw, stage="task")
    expected = _task(task)
    if raw != expected:
        raise Blocked("noncanonical_task_file", stage="task")
    if task["id"] != task_id:
        raise Blocked("task_path_identity_mismatch", stage="task")
    paths = allowed_paths(task["target"], repository_root=repository_root)
    head = reader.membership(queue_commit)
    context = {
        "schema": SCHEMA, "status": "CONTEXT_READY", "repository": bridge.REPOSITORY,
        "queue_branch": bridge.QUEUE_BRANCH, "queue_commit": queue_commit, "queue_head_sha": head,
        "task_path": "msw-coding-tasks/" + task_id + ".json", "task_id": task_id,
        "request_sha256": task["request_sha256"], "file_sha256": file_sha256, "task": task,
        "target": task["target"], "base_branch": "main", "base_sha": base_sha,
        "allowed_paths": paths, "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **{field: True for field in TRUE_FLAGS}, **{field: False for field in FALSE_FLAGS},
    }
    return validate_context(context, repository_root=repository_root)


def main(argv=None, environ=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("task-id", "queue-commit", "file-sha256", "base-sha"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--output", help="New public context file selected by the trusted workflow")
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    try:
        context = prepare_context(args.task_id, args.queue_commit, args.file_sha256, args.base_sha,
                                  token=env.get("GITHUB_TOKEN"))
        raw = context_bytes(context)
        if args.output:
            # Exclusive creation prevents replacing a symlink or an unrelated artifact.
            with open(args.output, "xb") as output:
                output.write(raw)
            receipt = {"schema": "msw.coding-task-context.receipt.v1", "status": context["status"],
                       "task_id": context["task_id"], "context_sha256": hashlib.sha256(raw).hexdigest(),
                       "execution_authorized": False}
            print(bridge.canonical(receipt).decode("utf-8"))
        else:
            print(raw.decode("utf-8"), end="")
        return 0
    except Blocked as exc:
        result = {"schema": SCHEMA, "status": "BLOCKED", "error": exc.code, "stage": exc.stage,
                  "execution_authorized": False, "production_hold": True}
        if exc.http_status is not None:
            result["http_status"] = exc.http_status
    except OSError:
        result = {"schema": SCHEMA, "status": "BLOCKED", "error": "context_output_failed",
                  "execution_authorized": False, "production_hold": True}
    except Exception:
        result = {"schema": SCHEMA, "status": "BLOCKED", "error": "unexpected_context_error",
                  "execution_authorized": False, "production_hold": True}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
