"""Read-only, pinned GitHub file verification for the existing MSW queue.

This module never executes downloaded content and never writes to GitHub. The
caller must already have an authenticated, capability-scoped queue. Matching
hashes prove file identity, not permission, correctness, tests or deployment.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .client import NoRedirect
from .tasks import TaskError

REPOSITORY = "flyxpressWarszawa/KonyWarsaw"
API = "https://api.github.com/repos/" + REPOSITORY
SCHEMA = "msw.github.verify-file.v1"
PREFIXES = (
    "msw-autonomous-engine/", "msw-github-engine/", "msw-ai-technical-core/",
    "msw-ai-centrum-robotow/", "msw-compute-node-adapters/", "msw-research-engine/",
    "msw-coding-bridge/", "msw-owned-llm/",
)
EXTENSIONS = {".py", ".js", ".mjs", ".cjs", ".json", ".md", ".txt", ".html", ".css", ".php"}
MAX_FILE = 256 * 1024
MAX_RESPONSE = 2 * 1024 * 1024
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
SEGMENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}\Z")
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{12,96}\Z")
TASK_PATH = re.compile(r"msw-coding-tasks/[0-9a-f]{32}\.json\Z")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _get(path: str) -> dict:
    # All paths are constructed inside this module from validated Git object
    # IDs; neither the request nor configuration can replace the API host.
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "MSW-GitHub-Verify/1",
               "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("MSW_GITHUB_READ_TOKEN", "")
    if token:
        if len(token) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise TaskError("github_invalid_read_token")
        headers["Authorization"] = "Bearer " + token
    request = Request(API + path, headers=headers, method="GET")
    try:
        with build_opener(NoRedirect).open(request, timeout=8) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise TaskError("github_response_too_large")
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        if not isinstance(obj, dict):
            raise ValueError("invalid object")
        return obj
    except HTTPError as error:
        # Do not include URLs, response bodies, auth headers, or token values.
        raise TaskError("github_http_" + str(error.code)) from None
    except (URLError, TimeoutError, OSError):
        raise TaskError("github_transport_unavailable") from None
    except (ValueError, UnicodeError):
        raise TaskError("github_invalid_response") from None


def _validate(payload: dict) -> tuple[str, str, str, str]:
    if not isinstance(payload, dict) or set(payload) != {
        "schema", "request_id", "commit_sha", "path", "expected_sha256"
    } or payload.get("schema") != SCHEMA:
        raise TaskError("github_invalid_job")
    rid, commit, path, expected = (payload[k] for k in ("request_id", "commit_sha", "path", "expected_sha256"))
    if not all(isinstance(x, str) for x in (rid, commit, path, expected)):
        raise TaskError("github_invalid_job")
    if not REQUEST_ID.fullmatch(rid) or not HEX40.fullmatch(commit) or not HEX64.fullmatch(expected):
        raise TaskError("github_invalid_identity")
    parts = path.split("/")
    if ((not path.startswith(PREFIXES) and not TASK_PATH.fullmatch(path)) or not 2 <= len(parts) <= 8
            or any(not SEGMENT.fullmatch(part) or part in {".", ".."} for part in parts)
            or any(part.lower() in {"data", "runtime", "secrets", "credentials", "node_modules", "__pycache__"} for part in parts)
            or ".env" in parts[-1].lower() or Path(parts[-1]).suffix.lower() not in EXTENSIONS):
        raise TaskError("github_path_not_allowed")
    return rid, commit, path, expected


def _tree_entry(tree_sha: str, segment: str, leaf: bool) -> str:
    value = _get("/git/trees/" + tree_sha)
    entries = value.get("tree")
    if value.get("sha") != tree_sha or value.get("truncated") is not False or not isinstance(entries, list):
        raise TaskError("github_tree_not_verified")
    matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("path") == segment]
    if len(matches) != 1:
        raise TaskError("github_path_not_found")
    entry = matches[0]
    expected_type = "blob" if leaf else "tree"
    allowed_modes = {"100644", "100755"} if leaf else {"040000"}
    sha = entry.get("sha")
    if entry.get("type") != expected_type or entry.get("mode") not in allowed_modes or not isinstance(sha, str) or not HEX40.fullmatch(sha):
        raise TaskError("github_tree_entry_forbidden")
    return sha


def verified_file(payload: dict, *, max_file: int = MAX_FILE) -> tuple[dict, bytes]:
    """Return verified metadata and bounded bytes to another trusted handler.

    The caller cannot choose an API host or skip the same public-path validation.
    Bytes are for in-process contract validation, never a public result field.
    """
    if type(max_file) is not int or not 1 <= max_file <= MAX_FILE:
        raise TaskError("github_invalid_size_limit")
    rid, commit, path, expected = _validate(payload)
    result = _get("/git/commits/" + commit)
    tree = result.get("tree")
    if (result.get("sha") != commit or not isinstance(tree, dict)
            or not isinstance(tree.get("sha"), str) or not HEX40.fullmatch(tree["sha"])):
        raise TaskError("github_commit_not_verified")
    tree_sha = tree["sha"]
    segments = path.split("/")
    object_sha = tree_sha
    for index, segment in enumerate(segments):
        object_sha = _tree_entry(object_sha, segment, index == len(segments) - 1)
    value = _get("/git/blobs/" + object_sha)
    if (value.get("sha") != object_sha or value.get("encoding") != "base64"
            or type(value.get("size")) is not int or not 0 <= value["size"] <= max_file
            or not isinstance(value.get("content"), str)):
        raise TaskError("github_blob_not_verified")
    try:
        encoded = "".join(value["content"].splitlines()).encode("ascii")
        if len(encoded) > ((max_file + 2) // 3) * 4:
            raise ValueError("blob too large")
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError, binascii.Error):
        raise TaskError("github_blob_not_verified") from None
    git_hash = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) != value["size"] or not hmac.compare_digest(git_hash, object_sha):
        raise TaskError("github_blob_hash_mismatch")
    if not hmac.compare_digest(digest, expected):
        raise TaskError("github_expected_sha256_mismatch")
    receipt = {"schema": "msw.github.verify-file.result.v1", "request_id": rid,
            "repository": REPOSITORY, "commit_sha": commit, "root_tree_sha": tree_sha,
            "path": path, "git_blob_sha": object_sha, "content_sha256": digest,
            "size": len(raw), "github_file_verified": True,
            "tests_verified": False, "deployed_verified": False}
    return receipt, raw


def handle(payload: dict, _data_dir: Path) -> dict:
    receipt, _raw = verified_file(payload)
    return receipt
