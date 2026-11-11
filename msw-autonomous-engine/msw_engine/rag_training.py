"""Bounded, owner-selected, public-rights-approved RAG snapshots for research weight training.

This module reads only the existing approved knowledge.sqlite3. It never indexes
legacy/owner-unreviewed archives, grants rights, logs source text or trains a model.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote

ID = re.compile(r"[A-Za-z0-9_:.-]{1,200}\Z")
RIGHTS = "msw-training-approved-public:v1:"
SENSITIVE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{10,}|Bearer\s+[A-Za-z0-9_.-]{8,}|"
    r"\b(?:api[_ -]?key|token|password|secret|hasło)\s*[:=]|"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|"
    r"\+48[ \-]?(?:\d[ \-]?){9}|\b\d{3}[ \-]?\d{3}[ \-]?\d{3}\b)",
    re.IGNORECASE,
)


def _ids(value: object) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise ValueError("training_explicit_ids_required")
    if any(not isinstance(x, str) or ID.fullmatch(x) is None for x in value):
        raise ValueError("training_id_invalid")
    if len(set(value)) != len(value):
        raise ValueError("training_duplicate_ids")
    return value


def snapshot(data_dir: Path, train_ids: object, validation_ids: object) -> dict:
    """Return a pinned immutable-content view, not a training permission grant.

    A signed owner-authorized training job must also include this snapshot hash.
    Each source must have been separately designated for public-data training.
    """
    train_ids = _ids(train_ids)
    validation_ids = _ids(validation_ids)
    if set(train_ids) & set(validation_ids):
        raise ValueError("training_holdout_overlap")
    dbfile = data_dir / "knowledge.sqlite3"
    if dbfile.is_symlink() or not dbfile.is_file():
        raise ValueError("training_approved_corpus_missing")
    dburi = "file:" + quote(str(dbfile.resolve()), safe="/") + "?mode=ro"
    records = []
    try:
        with sqlite3.connect(dburi, uri=True, timeout=5) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            for ident in train_ids + validation_ids:
                row = db.execute(
                    "SELECT title,body,digest,provenance FROM documents WHERE id=?", (ident,)
                ).fetchone()
                if row is None:
                    raise ValueError("training_document_missing")
                title, body, digest, provenance = row
                if not all(isinstance(s, str) for s in row):
                    raise ValueError("training_document_invalid")
                if not provenance.startswith(RIGHTS) or len(provenance) > 900:
                    raise ValueError("training_rights_not_verified")
                if not 20 <= len(body.encode("utf-8")) <= 8000 or len(title) > 250:
                    raise ValueError("training_source_size")
                if SENSITIVE.search(title + "\n" + body):
                    raise ValueError("training_sensitive_source")
                check = hashlib.sha256((title + "\0" + body).encode()).hexdigest()
                if digest != check:
                    raise ValueError("training_source_digest_changed")
                records.append({"id": ident, "digest": check, "text": title + "\n" + body})
    except sqlite3.Error as error:
        raise ValueError("training_approved_corpus_unreadable") from error
    train = records[:len(train_ids)]
    validation = records[len(train_ids):]
    if len({item["digest"] for item in records}) != len(records):
        raise ValueError("training_duplicate_source_content")
    train_text = "\n\n".join(x["text"] for x in train) + "\n"
    validation_text = "\n\n".join(x["text"] for x in validation) + "\n"
    if not 256 <= len(train_text.encode()) <= 32000 or not 128 <= len(validation_text.encode()) <= 16000:
        raise ValueError("training_corpus_too_small_or_large")
    manifest = {
        "schema": "msw.dualmemory.approved-rag-snapshot.v1",
        "training": [{"id": r["id"], "sha256": r["digest"]} for r in train],
        "validation": [{"id": r["id"], "sha256": r["digest"]} for r in validation],
    }
    raw = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "train_text": train_text,
        "validation_text": validation_text,
        "train_records": len(train),
        "validation_records": len(validation),
        "source_bytes": len(train_text.encode()) + len(validation_text.encode()),
        "manifest": manifest,
    }
