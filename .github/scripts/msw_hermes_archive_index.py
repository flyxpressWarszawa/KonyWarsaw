#!/usr/bin/env python3
"""Build deterministic public MSW source index from an already checked-out Git tree.

Only hashes and indexes public source files. Never contacts ChatGPT, WordPress,
Render or paid providers; never executes code inside a source document.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlsplit

REPO = "flyxpressWarszawa/KonyWarsaw"
BRANCH = "msw/hermes-knowledge-vault"
KNOWLEDGE = Path("msw-autonomous-engine/knowledge")
OUTPUT = KNOWLEDGE / "HERMES-GITHUB-AUTO-INDEX.json"
BATCH_RE = re.compile(r"public-source-research-batch-([0-9]{3})-[0-9]{8}\.json")
SENSITIVE_KEYS = {"key", "api_key", "apikey", "token", "access_token", "secret",
                  "password", "signature", "authorization", "credential"}
MAX_FILE_BYTES = 1024 * 1024
MAX_BATCHES = 250
MAX_ENTRIES = 1000


def _read(root: Path, rel: Path) -> bytes:
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("unsafe_relative_path")
    current = root
    for bit in rel.parts:
        current = current / bit
        if current.is_symlink():
            raise ValueError("symlink_source_forbidden")
    if not current.is_file() or current.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("file_missing_or_oversized:" + rel.as_posix())
    return current.read_bytes()


def _url_ok(url: object) -> bool:
    if not isinstance(url, str) or not 1 <= len(url) <= 2048:
        return False
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return False
    if parts.fragment:
        return False
    return not any(k.casefold() in SENSITIVE_KEYS for k, _ in parse_qsl(parts.query, keep_blank_values=True))


def _entry(path: Path, raw: bytes, kind: str, count: int | None = None) -> dict:
    relative = path.as_posix()
    entry = {
        "path": relative,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "source_kind": kind,
        "raw_url": f"https://raw.githubusercontent.com/{REPO}/refs/heads/{BRANCH}/{relative}",
    }
    if count is not None:
        entry["records"] = count
    return entry


def build(root: Path) -> dict:
    base = root / KNOWLEDGE
    if not base.is_dir() or base.is_symlink():
        raise ValueError("knowledge_directory_missing_or_symlink")
    filepaths = sorted([p.relative_to(root) for p in base.iterdir()
                        if p.is_file() and p.suffix in {".json", ".md", ".jsonl"} and p.name != OUTPUT.name])
    codepaths = [Path(p) for p in (
        "msw-autonomous-engine/msw_engine/free_fallback_policy.py",
        "msw-autonomous-engine/tests/test_free_fallback_policy.py",
        "msw-autonomous-engine/wordpress/release_candidates/CapabilityTelemetry.php",
        "msw-autonomous-engine/wordpress/release_candidates/tests/capability_telemetry_test.php",
        "msw-autonomous-engine/tools/msw_hermes_github_pull.py",
        "msw-autonomous-engine/tests/test_msw_hermes_github_pull.py",
    ) if (root / p).is_file()]
    filepaths.extend(codepaths)
    if len(filepaths) > MAX_ENTRIES:
        raise ValueError("too_many_sources")
    entries, seen_urls, batchids = [], set(), set()
    for rel in sorted(filepaths):
        raw = _read(root, rel)
        match = BATCH_RE.fullmatch(rel.name)
        if match is None:
            kind = "research_candidate" if "-candidates-" in rel.name else (
                "code_candidate" if rel.suffix in {".php", ".py"} else "source_only_document")
            entries.append(_entry(rel, raw, kind))
            continue
        index = int(match.group(1))
        if index in batchids:
            raise ValueError("batch_number_repeated")
        batchids.add(index)
        if len(batchids) > MAX_BATCHES:
            raise ValueError("too_many_batches")
        data = json.loads(raw.decode("utf-8"))
        rows = data.get("sources")
        if not isinstance(rows, list) or len(rows) != 100:
            raise ValueError("batch_not_100:" + rel.name)
        declared = data.get("source_count", data.get("count_saved"))
        if type(declared) is not int or declared != 100:
            raise ValueError("declared_source_count_mismatch:" + rel.name)
        if len({v.get("url") for v in rows if isinstance(v, dict)}) != 100:
            raise ValueError("duplicate_urls_in_batch:" + rel.name)
        for row in rows:
            if not isinstance(row, dict) or not _url_ok(row.get("url")):
                raise ValueError("invalid_source_url:" + rel.name)
            url = row["url"]
            if url in seen_urls:
                raise ValueError("url_reused_across_batches:" + rel.name)
            seen_urls.add(url)
        ent = _entry(rel, raw, "unverified_public_research", 100)
        ent["batch_number"] = index
        entries.append(ent)
    entries.sort(key=lambda d: (d["source_kind"], d["path"]))
    batches = sorted(batchids)
    return {
        "schema": "msw.hermes.github-auto-index.v1",
        "repository": REPO,
        "branch": BRANCH,
        "source_state": "GITHUB_ARCHIVED_NOT_HERMES_INGESTED",
        "completion_policy": "No automatic claim of Hermes receipt, provider availability, or trained weights.",
        "batch_count": len(batches),
        "batch_numbers": batches,
        "unique_public_source_urls": len(seen_urls),
        "files_indexed": len(entries),
        "source_items": entries,
        "privacy": "PUBLIC_METADATA_AND_CURATED_FILES_ONLY. Never store raw ChatGPT exports, secrets or customer content here.",
        "hermes_handoff": "Fetch exact raw URLs; compare SHA256 of original bytes; isolate unverified research; require signed ingest receipt/readback before success.",
        "unavailable_source": "Complete ChatGPT account history is not accessible to this GitHub Action.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True, help="checkout worktree of public MSW research branch")
    parser.add_argument("--check-only", action="store_true")
    options = parser.parse_args()
    root = options.root.resolve()
    result = build(root)
    output = root / OUTPUT
    wire = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    if options.check_only:
        print(json.dumps({"status": "VALIDATED", "public_docs": result["unique_public_source_urls"],
                          "batch_count": result["batch_count"], "files": result["files_indexed"]}))
        return 0
    if output.exists() and _read(root, OUTPUT) == wire:
        print(json.dumps({"status": "UNCHANGED", "public_docs": result["unique_public_source_urls"]}))
        return 0
    output.write_bytes(wire)
    print(json.dumps({"status": "UPDATED", "public_docs": result["unique_public_source_urls"],
                      "batch_count": result["batch_count"], "files": result["files_indexed"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
