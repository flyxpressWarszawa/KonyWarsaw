#!/usr/bin/env python3
"""Download and checksum public MSW/Hermes knowledge without executing it.

This is a stand-alone pull client. It does NOT write to the approved Hermes RAG,
touch WordPress, consume paid APIs, load credentials, or claim import ACK.
Integrate into the existing Render worker only after its receipt gate is repaired.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import ssl
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, HTTPSHandler, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError

REPO = "flyxpressWarszawa/KonyWarsaw"
BRANCH = "msw/hermes-knowledge-vault"
PREFIX = f"https://raw.githubusercontent.com/{REPO}/refs/heads/{BRANCH}/"
MANIFEST_PATH = "msw-autonomous-engine/knowledge/HERMES-GITHUB-AUTO-INDEX.json"
INDEX_URL = PREFIX + MANIFEST_PATH
MAX_INDEX_BYTES = 512_000
MAX_FILE_BYTES = 1_048_576
MAX_TOTAL_BYTES = 64_000_000
MAX_FILES = 1000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise HTTPError(request.full_url, code, "redirect_not_allowed", headers, fp)


def _download(url: str, limit: int) -> bytes:
    if not isinstance(url, str) or not url.startswith(PREFIX):
        raise ValueError("unexpected_source_origin")
    parsed = urlsplit(url)
    if parsed.hostname != "raw.githubusercontent.com" or parsed.query or parsed.fragment:
        raise ValueError("unexpected_url_components")
    opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(Request(url, headers={"User-Agent": "MSW-Hermes-SourcePull/1.0",
                                           "Accept": "application/json, text/plain"}), timeout=25) as result:
        if result.status != 200:
            raise ValueError("unexpected_http_status")
        data = result.read(limit + 1)
    if len(data) > limit:
        raise ValueError("source_too_large")
    return data


def _safe_path(relative: str) -> Path:
    if not isinstance(relative, str) or len(relative) > 280:
        raise ValueError("invalid_path")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
        raise ValueError("path_traversal")
    if not relative.startswith("msw-autonomous-engine/knowledge/"):
        raise ValueError("only_public_knowledge_files")
    if pure.suffix not in {".md", ".json", ".jsonl"}:
        raise ValueError("non_document_file")
    return Path(*pure.parts)


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    p = path.parent
    while p != path.parent.parent:
        if p.is_symlink():
            raise ValueError("symlink_parent_rejected")
        p = p.parent
    if path.is_symlink():
        raise ValueError("symlink_destination_rejected")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def pull(destination: Path, *, fetch=_download) -> dict:
    """Verified offline-public staging. Never admits documents as approved RAG."""
    root = destination.resolve()
    index_raw = fetch(INDEX_URL, MAX_INDEX_BYTES)
    index = json.loads(index_raw.decode("utf-8"))
    if (index.get("schema") != "msw.hermes.github-auto-index.v1" or
            index.get("repository") != REPO or index.get("branch") != BRANCH or
            index.get("source_state") != "GITHUB_ARCHIVED_NOT_HERMES_INGESTED"):
        raise ValueError("manifest_identity_mismatch")
    items = index.get("source_items")
    if not isinstance(items, list) or len(items) > MAX_FILES:
        raise ValueError("manifest_item_count_invalid")
    receipts, checked = [], set()
    total = 0
    for item in items:
        if not isinstance(item, dict) or item.get("source_kind") == "code_candidate":
            continue
        rel = _safe_path(item.get("path"))
        name = rel.as_posix()
        if name in checked:
            raise ValueError("duplicate_manifest_path")
        checked.add(name)
        address = PREFIX + name
        if item.get("raw_url") != address:
            raise ValueError("manifest_url_mismatch")
        expected = item.get("sha256", "")
        if not isinstance(expected, str) or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
            raise ValueError("invalid_source_sha")
        target = root / rel
        prior = target.read_bytes() if target.is_file() and not target.is_symlink() and target.stat().st_size <= MAX_FILE_BYTES else None
        if prior is not None and hashlib.sha256(prior).hexdigest() == expected:
            status, raw = "unchanged", prior
        else:
            raw = fetch(address, MAX_FILE_BYTES)
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError("source_digest_mismatch:" + name)
            _atomic_write(target, raw)
            status = "downloaded"
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("aggregate_source_limit")
        receipts.append({"path": name, "sha256": expected, "download_state": status,
                         "ingested_into_hermes": False})
    report = {
        "schema": "msw.hermes.source-download-receipt.v1",
        "status": "PUBLIC_DOWNLOAD_VERIFIED_NOT_HERMES_INGESTED",
        "files": len(receipts), "stored_bytes_checked": total,
        "reported_unique_url_count": index.get("unique_public_source_urls"),
        "receipts": receipts,
    }
    report_path = root / "HERMES-GITHUB-DOWNLOAD-RECEIPT.json"
    _atomic_write(report_path, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return {"status": report["status"], "files": report["files"],
            "bytes_checked": total, "source_urls": report["reported_unique_url_count"],
            "receipt": str(report_path)}


def main() -> int:
    args = argparse.ArgumentParser()
    args.add_argument("--dest", type=Path, required=True)
    options = args.parse_args()
    print(json.dumps(pull(options.dest), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
