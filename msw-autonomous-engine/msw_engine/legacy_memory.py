"""Restore a pinned legacy corpus as separately labelled, unreviewed memory.

Only an operator-approved runtime pack supplies data. Queue payloads cannot
select an archive or path. This restores source text; it never trains an LLM.
"""
from __future__ import annotations

import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from .runtime_pack import module_root

MAX_BYTES = 32 * 1024 * 1024
MAX_ROWS = 30_000
STATUS = "legacy-source-status.json"


def digest_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(part)
    return value.hexdigest()


def status(directory: Path) -> dict:
    try:
        value = json.loads((directory / STATUS).read_text())
        return {key: value[key] for key in ("state", "records", "source_archive_sha256", "source_sha256", "database_sha256", "review_state", "cache_reused", "error") if key in value}
    except (OSError, ValueError):
        return {"state": "not_configured"}


def restore(directory: Path) -> dict:
    root = module_root("malta-source-memory")
    if root is None:
        return {"state": "not_configured"}
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    value = {"state": "blocked", "records": 0, "review_state": "legacy_unreviewed"}
    temporary = None
    try:
        metadata = json.loads((root / "corpus-manifest.json").read_text())
        expected, count, length = (metadata[key] for key in ("uncompressed_sha256", "records", "uncompressed_bytes"))
        archive_digest = metadata["source_archive_sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", expected) or not re.fullmatch(r"[a-f0-9]{64}", archive_digest):
            raise ValueError("corpus_manifest_digest")
        if type(count) is not int or not 1 <= count <= MAX_ROWS or type(length) is not int or not 1 <= length <= MAX_BYTES:
            raise ValueError("corpus_manifest_limit")
        if metadata.get("review_state") != "legacy_unreviewed" or metadata.get("model_weights_included") is not False:
            raise ValueError("corpus_review_state")
        compressed = root / "knowledge.jsonl.xz"
        if digest_file(compressed) != metadata["compressed_sha256"]:
            raise ValueError("corpus_compressed_digest")
        target = directory / ("legacy-malta-" + expected + ".sqlite3")
        previous = status(directory)
        if target.is_symlink():
            raise ValueError("corpus_database_symlink")
        if (target.is_file() and previous.get("state") == "ready" and previous.get("source_sha256") == expected
            and previous.get("records") == count and previous.get("source_archive_sha256") == archive_digest
            and digest_file(target) == previous.get("database_sha256")):
            value = {**previous, "cache_reused": True}
            return value
        fd, temporary_name = tempfile.mkstemp(prefix=".legacy-malta-", suffix=".sqlite3", dir=directory)
        os.close(fd)
        temporary = Path(temporary_name)
        db = sqlite3.connect(temporary)
        received, rows, source_hash = 0, 0, hashlib.sha256()
        try:
            db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY,title TEXT NOT NULL,body TEXT NOT NULL,source TEXT NOT NULL,kind TEXT NOT NULL,digest TEXT NOT NULL)")
            db.execute("CREATE VIRTUAL TABLE document_fts USING fts5(id UNINDEXED,title,body,tokenize='unicode61 remove_diacritics 2')")
            with db, compressed.open("rb") as stream:
                decoder = lzma.LZMADecompressor(memlimit=64 * 1024 * 1024)
                pending = b""
                while True:
                    chunk = stream.read(64 * 1024) if decoder.needs_input else b""
                    if not chunk and decoder.needs_input:
                        if not decoder.eof:
                            raise ValueError("corpus_compression_truncated")
                        break
                    data = decoder.decompress(chunk, max_length=1024 * 1024)
                    received += len(data)
                    if received > length or received > MAX_BYTES:
                        raise ValueError("corpus_expanded_limit")
                    source_hash.update(data)
                    pending += data
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        if line.strip():
                            rows += insert_row(db, line)
                            if rows > count or rows > MAX_ROWS:
                                raise ValueError("corpus_row_limit")
                    if len(pending) > 200_000:
                        raise ValueError("corpus_line_limit")
                    if decoder.eof:
                        if decoder.unused_data or stream.read(1):
                            raise ValueError("corpus_trailing_data")
                        break
                if pending.strip():
                    rows += insert_row(db, pending)
                if received != length or source_hash.hexdigest() != expected or rows != count:
                    raise ValueError("corpus_source_readback_mismatch")
            if db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] != count or db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("corpus_database_readback_mismatch")
        finally:
            db.close()
        temporary.chmod(0o600)
        temporary.replace(target)
        value = {"state": "ready", "records": count, "source_archive_sha256": archive_digest,
                 "source_sha256": expected, "database_sha256": digest_file(target),
                 "review_state": "legacy_unreviewed", "cache_reused": False}
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, lzma.LZMAError) as error:
        value = {"state": "blocked", "records": 0, "review_state": "legacy_unreviewed",
                 "error": str(error)[:80] if isinstance(error, ValueError) else type(error).__name__}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        output = directory / ("." + STATUS + ".tmp")
        output.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        output.chmod(0o600)
        output.replace(directory / STATUS)
    return value


def insert_row(db, line: bytes) -> int:
    if len(line) > 200_000:
        raise ValueError("corpus_line_limit")
    row = json.loads(line)
    if not isinstance(row, dict) or set(row) != {"id", "title", "content", "source", "kind"} or type(row["id"]) is not int or row["id"] <= 0:
        raise ValueError("corpus_row_schema")
    for key, limit in (("title", 1000), ("content", 100_000), ("source", 1000), ("kind", 100)):
        if not isinstance(row[key], str) or len(row[key]) > limit:
            raise ValueError("corpus_field_limit")
    digest = hashlib.sha256((row["title"] + "\0" + row["content"]).encode()).hexdigest()
    db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)", (row["id"], row["title"], row["content"], row["source"], row["kind"], digest))
    db.execute("INSERT INTO document_fts VALUES(?,?,?)", (row["id"], row["title"], row["content"]))
    return 1


def search(payload: dict, directory: Path) -> dict:
    from .tasks import TaskError, WORD
    info = status(directory)
    if info.get("state") != "ready":
        raise TaskError("legacy_corpus_not_ready")
    words = WORD.findall(str(payload.get("query", ""))[:2000].casefold())[:20]
    limit = max(1, min(20, int(payload.get("limit", 5))))
    if not words:
        return {"items": [], "corpus": "legacy_malta", "review_state": "legacy_unreviewed"}
    expression = " OR ".join('"' + word.replace('"', '') + '"' for word in words)
    filename = directory / ("legacy-malta-" + info["source_sha256"] + ".sqlite3")
    db = sqlite3.connect(filename.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute("SELECT d.*,bm25(document_fts) AS score FROM document_fts JOIN documents d ON d.id=document_fts.id WHERE document_fts MATCH ? ORDER BY score LIMIT ?", (expression, limit)).fetchall()
        items = [{"id": "legacy:malta:" + str(row["id"]), "title": row["title"], "excerpt": row["body"][:600],
                  "source": row["source"], "source_kind": row["kind"], "sha256": row["digest"], "review_state": "legacy_unreviewed"} for row in rows]
        return {"items": items, "corpus": "legacy_malta", "records_total": info["records"],
                "source_archive_sha256": info["source_archive_sha256"], "review_state": "legacy_unreviewed",
                "training_performed": False, "measurement": "Lexical retrieval of legacy source text; factual claims require review."}
    finally:
        db.close()
