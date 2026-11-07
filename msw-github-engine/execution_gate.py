#!/usr/bin/env python3
"""Prepare prompt outputs while keeping the unimplemented execution grant closed.

This is a deliberate integration boundary, not a grant issuer. Transport proof,
repository variables, task text and environment variables cannot enable a model.
Replacing this boundary requires the agreed backend, single-use permission and
fresh STOP/HOLD checks. No production endpoint is guessed or called here.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys

import task_context


SCHEMA = "msw.coding-execution-gate.v1"
MAX_CONTEXT_BYTES = 65536


def read_context(path, expected_sha256):
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise ValueError("invalid_context_pin")
    source = Path(path)
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 1 <= info.st_size <= MAX_CONTEXT_BYTES:
        raise ValueError("invalid_context_file")
    raw = source.read_bytes()
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256):
        raise ValueError("context_pin_mismatch")
    context = task_context.bridge.decode_json(raw)
    if raw != task_context.context_bytes(context):
        raise ValueError("noncanonical_context")
    return context


def decision(context, context_sha256):
    task_context.validate_context(context)
    if hashlib.sha256(task_context.context_bytes(context)).hexdigest() != context_sha256:
        raise ValueError("context_pin_mismatch")
    return {
        "schema": SCHEMA,
        "status": "HELD",
        "execution_permitted": False,
        "publication_permitted": False,
        "reason": "execution_backend_not_integrated",
        "task_id": context["task_id"],
        "context_sha256": context_sha256,
        "production_hold": True,
        "model_called": False,
        "production_changed": False,
    }


def outputs(context, context_sha256):
    gate = decision(context, context_sha256)
    # ASCII JSON is one physical line even for hostile newlines, delimiters,
    # Unicode line separators or GitHub-looking expressions in task data.
    prompt = json.dumps(context, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {
        "context_sha256": context_sha256,
        "prompt_context": prompt,
        "task_id": context["task_id"],
        "target": context["target"],
        "branch": "msw-coding-task/" + context["task_id"],
        "execution_permitted": "false",
        "publication_permitted": "false",
        "reason": gate["reason"],
    }


def write_outputs(path, values):
    # No user-selected keys or delimiter protocol. Each value must already be a
    # single ASCII line; append exactly the fixed keys generated above.
    if set(values) != {"context_sha256", "prompt_context", "task_id", "target", "branch",
                       "execution_permitted", "publication_permitted", "reason"}:
        raise ValueError("invalid_workflow_output_keys")
    for key, value in values.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or not isinstance(value, str) or any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise ValueError("invalid_workflow_output")
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a", encoding="ascii", newline="\n") as target:
        info = os.fstat(target.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("invalid_workflow_output_file")
        target.write("".join(key + "=" + value + "\n" for key, value in values.items()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--expected-context-sha256", required=True)
    parser.add_argument("--output", required=True, help="New public decision artifact")
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args(argv)
    try:
        context = read_context(args.context, args.expected_context_sha256)
        gate = decision(context, args.expected_context_sha256)
        raw = task_context.bridge.canonical(gate) + b"\n"
        with open(args.output, "xb") as target:
            target.write(raw)
        if args.github_output:
            write_outputs(args.github_output, outputs(context, args.expected_context_sha256))
        print(raw.decode("utf-8"), end="")
        return 0
    except Exception:
        print(json.dumps({"schema": SCHEMA, "status": "BLOCKED", "execution_permitted": False,
                          "publication_permitted": False, "reason": "execution_context_invalid",
                          "model_called": False, "production_changed": False}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    sys.exit(main())
