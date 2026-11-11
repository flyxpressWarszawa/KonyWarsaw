"""Offline staging for explicitly reviewed MSW Qwen SFT examples.

No Alibaba calls, automatic training, customer data harvesting or production
writes. Requires operator-verified, rights-cleared nonpersonal examples.
Writes only to an explicitly requested private location, never into Git.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

SCHEMA = "msw.sft.reviewed-example.v1"
MAX_ROWS = 1000
MAX_INPUT = 4_000_000
_ALLOWED = frozenset({"id", "schema", "question", "approved_answer",
                      "source_text", "source_sha256", "owner_approved",
                      "rights_verified", "privacy_verified", "technical_reviewed",
                      "classification"})
SENSITIVE = re.compile(
    r"(?:sk-sp-|sk-[A-Za-z0-9_-]{12,}|o1_[A-Za-z0-9_-]{12,}|"
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|"
    r"\b(?:\+?\d[\d ().-]{8,}\d)\b)", re.I
)
SYSTEM = ("Jesteś ekspertem technicznym MSW. Odpowiadaj wyłącznie na podstawie "
          "zatwierdzonych danych. Nie wymyślaj parametrów, cen ani realizacji. "
          "Wskazuj potrzebę weryfikacji przez technika.")


def prepare(raw):
    if not isinstance(raw, list) or not 2 <= len(raw) <= MAX_ROWS:
        raise ValueError("approved_dataset_count_invalid")
    examples, seen = [], set()
    for row in raw:
        if not isinstance(row, dict) or set(row) - _ALLOWED:
            raise ValueError("unrecognized_record_fields")
        if row.get("schema") != SCHEMA or row.get("classification") != "owner_approved_nonpersonal":
            raise ValueError("unapproved_source_classification")
        if any(row.get(flag) is not True for flag in
               ("owner_approved", "rights_verified", "privacy_verified", "technical_reviewed")):
            raise ValueError("source_approvals_missing")
        ident = row.get("id")
        if not isinstance(ident, str) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{6,90}", ident) or ident in seen:
            raise ValueError("source_identity_invalid")
        seen.add(ident)
        q, a, source = (row.get(k) for k in ("question", "approved_answer", "source_text"))
        if not all(isinstance(x, str) and 10 <= len(x.strip()) <= 3000 for x in (q, a, source)):
            raise ValueError("source_text_limit")
        if SENSITIVE.search(q) or SENSITIVE.search(a) or SENSITIVE.search(source):
            raise ValueError("potential_personal_or_secret_content")
        digest = row.get("source_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("source_sha_invalid")
        if hashlib.sha256(source.encode("utf-8")).hexdigest() != digest:
            raise ValueError("source_digest_mismatch")
        examples.append({"messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": q},
            {"role": "assistant", "content": a}
        ]})
    # Deterministic split; examples stay in caller order, no shuffle over
    # private text and no giant randomly consumed provider context.
    val_count = max(1, len(examples) // 10)
    return examples[val_count:], examples[:val_count]


def _private_output_dir(folder):
    folder = Path(folder).expanduser().resolve()
    if ".git" in folder.parts or folder.name.lower() in {"public_html", "uploads"}:
        raise ValueError("unsafe_output_directory")
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.is_symlink():
        raise ValueError("symlink_output")
    os.chmod(folder, 0o700)
    return folder


def write_private(train, validation, folder):
    target = _private_output_dir(folder)
    for name, entries in (("msw-sft-train.jsonl", train), ("msw-sft-validation.jsonl", validation)):
        path = target / name
        if path.exists() or path.is_symlink():
            raise ValueError("output_exists")
        with path.open("x", encoding="utf-8") as stream:
            os.chmod(path, 0o600)
            for item in entries:
                stream.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    return {"state": "OFFLINE_STAGED_NO_UPLOAD", "training_examples": len(train),
            "validation_examples": len(validation), "provider_training_started": False,
            "external_upload_performed": False}


def main(args=None):
    p = argparse.ArgumentParser()
    p.add_argument("--reviewed-records", required=True)
    p.add_argument("--private-output-dir")
    options = p.parse_args(args)
    source = Path(options.reviewed_records).expanduser()
    if source.stat().st_size > MAX_INPUT:
        raise ValueError("input_limit")
    train, val = prepare(json.loads(source.read_text(encoding="utf-8")))
    result = (write_private(train, val, options.private_output_dir) if options.private_output_dir
              else {"state": "READY_TO_STAGE_NOT_UPLOADED",
                    "training_examples": len(train), "validation_examples": len(val),
                    "provider_training_started": False, "external_upload_performed": False})
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError):
        # No raw private record or credential is printed.
        print(json.dumps({"state": "NOT_READY", "provider_training_started": False}), file=sys.stderr)
        sys.exit(2)
