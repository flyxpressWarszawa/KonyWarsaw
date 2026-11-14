"""Read-only cross-check of the queue commit and its archived result.

An archived receipt can exist before the queue CAS commits. Neither that file
alone nor a hash proves that Hermes read a real ChatGPT answer. This module
checks storage consistency only; it never claims, reports, ACKs, or sends chats.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Protocol

QUEUE_SCHEMA = "msw.hermes.chat.queue.v1"
RECEIPT_SCHEMA = "msw.hermes.chat.receipt.v1"
ROOT = "knowledge/hermes-chat-loop"
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,119}\Z")
DIGEST = re.compile(r"[a-f0-9]{64}\Z")
BLOB = re.compile(r"[a-f0-9]{40}\Z")
OUTCOMES = {"completed", "blocked", "approval_required"}
EXECUTORS = {"hermes", "chatgpt_scheduled"}


class ReceiptAuditError(RuntimeError):
    """Bounded internal error code, never private result text or credentials."""


class QueueReader(Protocol):
    def _read_queue(self) -> tuple[dict, str]: ...
    def _archived_receipt(self, task_id: str) -> dict | None: ...


def _fail(code: str) -> None:
    raise ReceiptAuditError(code)


def _matches(pattern: re.Pattern, value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _wire(value: dict) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        _fail("audit_document_invalid")


def _check_result(record: dict) -> None:
    text, digest = record.get("result"), record.get("result_sha256")
    if not isinstance(text, str) or not text.strip() or not _matches(DIGEST, digest):
        _fail("audit_result_invalid")
    try:
        raw = text.encode("utf-8")
    except UnicodeError:
        _fail("audit_result_invalid")
    if len(raw) > 16_384 or hashlib.sha256(raw).hexdigest() != digest:
        _fail("audit_result_integrity")
    if not isinstance(record.get("outcome"), str) or record["outcome"] not in OUTCOMES:
        _fail("audit_outcome_invalid")


def _check_ack(ack: dict, receipt: dict | None, conversation: str) -> None:
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        _fail("audit_ack_receipt_missing")
    fields = ("task_id", "work_key", "executor", "parent_id", "depth",
              "request_sha256", "result_sha256", "outcome")
    if any(ack.get(key) != receipt.get(key) for key in fields):
        _fail("audit_ack_receipt_mismatch")
    if receipt.get("conversation_id") != conversation:
        _fail("audit_receipt_conversation_mismatch")
    if (not _matches(IDENTIFIER, ack.get("work_key"))
            or not isinstance(ack.get("executor"), str) or ack["executor"] not in EXECUTORS
            or not _matches(DIGEST, ack.get("request_sha256"))
            or type(ack.get("depth")) is not int or not 1 <= ack["depth"] <= 12
            or (ack.get("parent_id") is not None
                and not _matches(IDENTIFIER, ack["parent_id"]))):
        _fail("audit_ack_metadata_invalid")
    expected_path = f"{ROOT}/receipts/{ack['task_id']}.json"
    if ack.get("receipt_path") != expected_path:
        _fail("audit_receipt_path_mismatch")
    marker = receipt.get("hermes_ack")
    if not isinstance(marker, dict) or marker.get("verified") is not True:
        _fail("audit_receipt_not_marked")
    if (not isinstance(marker.get("at_utc"), str) or not marker["at_utc"]
            or ack.get("acknowledged_at_utc") != marker["at_utc"]):
        _fail("audit_ack_time_mismatch")
    _check_result(receipt)


def audit_snapshot(queue: QueueReader) -> dict:
    """Read a bounded snapshot twice; no write-capable method is called.

    ``queue_ack_committed`` attests only matching stored records. Actor identity,
    a real model answer, and direct personal-chat delivery stay unverified.
    """
    doc, snapshot_sha = queue._read_queue()
    if (not isinstance(doc, dict) or doc.get("schema") != QUEUE_SCHEMA
            or type(doc.get("protocol_version")) is not int or doc["protocol_version"] != 1
            or type(doc.get("revision")) is not int or doc["revision"] < 0
            or not _matches(BLOB, snapshot_sha)):
        _fail("audit_queue_invalid")
    channel, tasks = doc.get("channel"), doc.get("tasks")
    if not isinstance(channel, dict) or not _matches(IDENTIFIER, channel.get("conversation_id")):
        _fail("audit_channel_invalid")
    if not isinstance(tasks, list) or len(tasks) > 1:
        _fail("audit_tasks_invalid")
    conversation = channel["conversation_id"]
    before = _wire(doc)
    states = {"pending", "claimed", "reported", "uncertain"}
    counts = {}
    for task in tasks:
        if (not isinstance(task, dict) or not isinstance(task.get("state"), str)
                or task["state"] not in states
                or not _matches(IDENTIFIER, task.get("task_id"))
                or task.get("conversation_id") != conversation):
            _fail("audit_task_invalid")
        counts[task["state"]] = 1
        if task["state"] == "reported":
            _check_result(task)
    ack = doc.get("last_ack")
    if ack is not None:
        if not isinstance(ack, dict) or not _matches(IDENTIFIER, ack.get("task_id")):
            _fail("audit_ack_invalid")
        if any(task["task_id"] == ack["task_id"] for task in tasks):
            _fail("audit_ack_still_active")
        _check_ack(ack, queue._archived_receipt(ack["task_id"]), conversation)
        counts["acknowledged"] = 1
    latest, latest_sha = queue._read_queue()
    if latest_sha != snapshot_sha or _wire(latest) != before:
        _fail("audit_snapshot_changed")
    return {
        "schema": "msw.hermes.chat.receipt-audit.v1",
        "scope": "storage_consistency_only",
        "queue_revision": doc["revision"],
        "queue_ack_committed": ack is not None,
        "receipt_integrity_verified": ack is not None,
        "counts": counts,
        "hermes_identity_verified": False,
        "live_external_chat_connected": False,
        "new_chat_verified": False,
        "existing_chat_verified": False,
    }
