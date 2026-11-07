"""Read-only coding task delivery verification using the PR #3 GitHub verifier.

This handler is an inert candidate until the existing authenticated server queue
grants its capability. It creates no queue, model call, scheduler or deployment.
Transport receipts are inputs to verify, not authorization or execution proof.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re

from . import github_verify
from .tasks import TaskError

SCHEMA = "msw.coding-verify-delivery.v1"
RESULT_SCHEMA = "msw.coding-verify-delivery.result.v1"
QUEUE_BRANCH = "msw/coding-queue"
TASK_SCHEMA = "msw.coding-task.v1"
MAX_TASK = 16384
HEX32 = re.compile(r"[0-9a-f]{32}\Z")
TARGETS = {"technical-core", "portal", "compute-adapters", "research", "runtime", "owned-llm", "wordpress"}
BOUND_KEYS = ("repository", "queue_branch", "task_id", "request_sha256", "commit_sha", "task_path", "file_sha256")
REQUEST_KEYS = {"schema", "request_id", *BOUND_KEYS}
TASK_KEYS = {"schema", "id", "request_id", "source_engine", "target", "title", "objective", "acceptance",
             "public_data", "created_at", "request_sha256"}
SECRET_RE = re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{35,}|AKIA[A-Z0-9]{16})\b")


def _canonical(value: dict) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise TaskError("coding_invalid_json") from None


def _validate_request(payload: dict) -> None:
    if not isinstance(payload, dict) or set(payload) != REQUEST_KEYS or payload.get("schema") != SCHEMA:
        raise TaskError("coding_invalid_verification_request")
    if payload["repository"] != github_verify.REPOSITORY or payload["queue_branch"] != QUEUE_BRANCH:
        raise TaskError("coding_repository_or_branch")
    patterns = {"request_id": github_verify.REQUEST_ID, "task_id": HEX32,
                "request_sha256": github_verify.HEX64, "commit_sha": github_verify.HEX40,
                "file_sha256": github_verify.HEX64}
    if any(not isinstance(payload[key], str) or not pattern.fullmatch(payload[key]) for key, pattern in patterns.items()):
        raise TaskError("coding_invalid_verification_identity")
    if payload["task_path"] != "msw-coding-tasks/" + payload["task_id"] + ".json":
        raise TaskError("coding_task_path_mismatch")


def verification_request(receipt: dict, request_id: str) -> dict:
    """Map a transport receipt to a bounded request; this does not enqueue it."""
    if (not isinstance(receipt, dict) or receipt.get("schema") != "msw.coding-bridge.receipt.v1"
            or not isinstance(receipt.get("state"), str)
            or receipt.get("state") not in {"delivered", "stored_unacknowledged"}):
        raise TaskError("coding_transport_receipt_unconfirmed")
    request = {key: receipt.get(key) for key in BOUND_KEYS}
    request.update(schema=SCHEMA, request_id=request_id)
    _validate_request(request)
    return request


def _task(raw: bytes, expected: dict) -> dict:
    if len(raw) > MAX_TASK:
        raise TaskError("coding_task_too_large")
    try:
        def invalid_constant(_value):
            raise ValueError("nonfinite JSON")
        task = json.loads(raw.decode("utf-8"), object_pairs_hook=github_verify._pairs, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise TaskError("coding_invalid_task_json") from None
    if not isinstance(task, dict) or set(task) != TASK_KEYS or task.get("schema") != TASK_SCHEMA:
        raise TaskError("coding_invalid_task_schema")
    if task["public_data"] is not True:
        raise TaskError("coding_task_not_public")
    if task["id"] != expected["task_id"] or task["request_sha256"] != expected["request_sha256"]:
        raise TaskError("coding_task_identity_mismatch")
    if not isinstance(task["source_engine"], str) or not re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", task["source_engine"]):
        raise TaskError("coding_invalid_source_engine")
    if not isinstance(task["request_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", task["request_id"]):
        raise TaskError("coding_invalid_task_request_id")
    ident = hashlib.sha256((task["source_engine"] + ":" + task["request_id"]).encode("utf-8")).hexdigest()[:32]
    if ident != task["id"]:
        raise TaskError("coding_task_id_mismatch")
    if not isinstance(task["target"], str) or task["target"] not in TARGETS:
        raise TaskError("coding_invalid_target")
    for key, maximum in (("title", 120), ("objective", 4000)):
        if not isinstance(task[key], str) or not 1 <= len(task[key]) <= maximum or not task[key].strip():
            raise TaskError("coding_invalid_task_text")
    criteria = task["acceptance"]
    if (not isinstance(criteria, list) or not 1 <= len(criteria) <= 8
            or any(not isinstance(item, str) or not 1 <= len(item) <= 400 or not item.strip() for item in criteria)):
        raise TaskError("coding_invalid_acceptance")
    created = task["created_at"]
    if not isinstance(created, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", created):
        raise TaskError("coding_invalid_timestamp")
    try:
        datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise TaskError("coding_invalid_timestamp") from None
    digest = hashlib.sha256(_canonical({key: value for key, value in task.items() if key != "request_sha256"})).hexdigest()
    if not hmac.compare_digest(digest, expected["request_sha256"]):
        raise TaskError("coding_task_digest_mismatch")
    if raw != _canonical(task) + b"\n":
        raise TaskError("coding_task_not_canonical")
    if SECRET_RE.search(raw.decode("utf-8")):
        raise TaskError("coding_secret_like_content")
    return task


def _branch_membership(commit: str) -> str:
    """Verify ancestry against one freshly observed, fixed queue branch head."""
    ref = github_verify._get("/git/ref/heads/" + QUEUE_BRANCH)
    obj = ref.get("object", {})
    head = obj.get("sha") if isinstance(obj, dict) else None
    if (ref.get("ref") != "refs/heads/" + QUEUE_BRANCH or not isinstance(obj, dict)
            or obj.get("type") != "commit" or not isinstance(head, str) or not github_verify.HEX40.fullmatch(head)):
        raise TaskError("coding_queue_ref_not_verified")
    if head != commit:
        compared = github_verify._get("/compare/" + commit + "..." + head + "?per_page=1")
        base = compared.get("base_commit", {})
        ancestor = compared.get("merge_base_commit", {})
        if (compared.get("status") != "ahead" or not isinstance(base, dict) or base.get("sha") != commit
                or not isinstance(ancestor, dict) or ancestor.get("sha") != commit
                or type(compared.get("ahead_by")) is not int or compared["ahead_by"] < 1
                or type(compared.get("behind_by")) is not int or compared["behind_by"] != 0):
            raise TaskError("coding_commit_not_on_queue_branch")
    return head


def handle(payload: dict, _data_dir: Path) -> dict:
    _validate_request(payload)
    metadata, raw = github_verify.verified_file({
        "schema": github_verify.SCHEMA, "request_id": payload["request_id"],
        "commit_sha": payload["commit_sha"], "path": payload["task_path"],
        "expected_sha256": payload["file_sha256"],
    }, max_file=MAX_TASK)
    task = _task(raw, payload)
    queue_head = _branch_membership(payload["commit_sha"])
    return {
        "schema": RESULT_SCHEMA, "request_id": payload["request_id"],
        **{key: payload[key] for key in BOUND_KEYS},
        "git_blob_sha": metadata["git_blob_sha"], "root_tree_sha": metadata["root_tree_sha"],
        "size": metadata["size"], "queue_head_sha": queue_head,
        "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target": task["target"], "source_engine": task["source_engine"],
        "github_file_verified": True, "task_identity_verified": True,
        "queue_branch_membership_verified": True, "canonical_task_verified": True,
        "wordpress_ack_verified": False, "tests_verified": False,
        "code_executed": False, "deployed_verified": False,
    }
