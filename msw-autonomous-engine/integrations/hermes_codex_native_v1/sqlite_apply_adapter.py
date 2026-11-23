"""Adapter for one existing Hermes SQLite job; no dispatch or automatic retries.

The external executor must supply a verified Codex receipt and the original lease
token. The adapter never creates a task, changes executor or reclaims expired work.
"""
from __future__ import annotations

from asyncio import CancelledError
import hashlib
import json
import os
import re
import stat
import time
from contextlib import closing
from pathlib import Path
from unicodedata import category

from msw_engine.hermes_chat_bridge import _connection, _sha, _id


class ApplyHold(RuntimeError):
    pass


def canonical(value):
    # The SQLite adapter is also a direct entrypoint. Do not let json.dumps
    # silently coerce integer object keys to strings or tuples to arrays: the
    # resulting receipt digest would attest different in-memory evidence.
    # Validate recursively before opening or reporting to the local backend.
    def require_json_shape(item):
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise TypeError("non_string_json_object_key")
            for child in item.values():
                require_json_shape(child)
        elif type(item) is list:
            for child in item:
                require_json_shape(child)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise TypeError("non_json_container_type")

    require_json_shape(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def apply_once(directory, job, lease_token, codex_receipt):
    """Atomically persist a verified text result, then independently read back.

    A successful database commit may precede a transport timeout. In that case
    caller must inspect the original task, never dispatch a second execution.
    """
    # Local row validation and routed report/ACK must use the same backend.
    # Refuse before _connection can create or migrate an unrelated SQLite DB.
    if os.environ.get("MSW_HERMES_CHAT_BACKEND", "sqlite") != "sqlite":
        raise ApplyHold("sqlite_backend_required")
    fields = ("root_id", "task_id", "request_id", "work_key", "parent_id")
    if not isinstance(job, dict) or any(not isinstance(job.get(k), str) or not job[k] for k in fields):
        raise ApplyHold("invalid_identity")
    # The SQLite jobs table does not store root_id or request_id. In addition
    # to the five receipt IDs above, compare these immutable submit/claim
    # fields against the PERSISTED original row (not another caller echo).
    # This narrows cross-request confusion; it is NOT native root attestation.
    origin = ("conversation_key", "executor", "request_sha256")
    if (any(not isinstance(job.get(k), str) or not job[k] for k in origin)
            or re.fullmatch(r"[0-9a-f]{64}", job["request_sha256"]) is None):
        raise ApplyHold("origin_binding_required")
    if not isinstance(lease_token, str) or not 20 <= len(lease_token) <= 256:
        raise ApplyHold("invalid_lease")
    # Validate lease encoding before opening SQLite. A malformed Unicode token
    # must produce a stable HOLD rather than an uncaught encoder exception.
    try:
        lease_token_digest = _sha(lease_token)
    except UnicodeError as exc:
        raise ApplyHold("invalid_lease") from exc
    if not isinstance(codex_receipt, dict) or codex_receipt.get("status") != "VERIFIED":
        raise ApplyHold("receipt_unverified")
    # The adapter is also importable directly; enforce the same request binding
    # as its entrypoint so bypassing the wrapper cannot weaken admission.
    for field in ("task_id", "work_key", "root_id", "request_id", "parent_id"):
        if codex_receipt.get(field) != job[field]:
            raise ApplyHold("receipt_" + field + "_mismatch")
    if (codex_receipt.get("independent_readback") is not True
            or type(codex_receipt.get("tools")) is not int
            or codex_receipt["tools"] != 0):
        # bool(False) and float(0.0) compare equal to 0; neither proves no tools.
        raise ApplyHold("receipt_unsafe")
    result = codex_receipt.get("text")
    # Direct SQLite entrypoint must enforce the same actionable-result gate as
    # native_apply_bridge: strip() alone accepts Unicode Cf controls (BOM,
    # zero-width space, bidi marks) as a hash-valid but content-free result.
    # Refuse before touching the SQLite database or the existing report path.
    if not isinstance(result, str) or not any(
        not (ch.isspace() or category(ch) in ("Cf", "Cc")) for ch in result
    ):
        raise ApplyHold("result_invalid")
    # This direct adapter is callable without the public guarded entrypoint.
    # An unpaired Unicode surrogate, NaN/Infinity, non-JSON object, or cyclic
    # receipt must be a stable HOLD BEFORE opening the SQLite connection.
    # It must never appear as an uncaught runtime error that could invite retry.
    try:
        if len(result.encode("utf-8", errors="strict")) > 16384:
            raise ApplyHold("result_invalid")
        receipt_body = {k: v for k, v in codex_receipt.items() if k != "receipt_sha256"}
        receipt_digest = _sha(canonical(receipt_body))
        digest = _sha(result)
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        raise ApplyHold("receipt_noncanonical") from exc
    if receipt_digest != codex_receipt.get("receipt_sha256"):
        raise ApplyHold("receipt_digest_mismatch")
    if digest != codex_receipt.get("response_sha256"):
        raise ApplyHold("result_digest_mismatch")
    # _connection() creates/migrates storage as a side effect. This adapter
    # reports an EXISTING leased task and must not bootstrap a fresh database
    # when a caller points at the wrong directory. This is a preflight only:
    # the subsequent transactional handler remains the authority for lease/CAS.
    storage_base = Path(directory)
    storage_root = storage_base / "hermes-chat-loop"
    storage_file = storage_root / "exchange.sqlite3"
    # Checking only the last two path components misses a symlink in
    # directory or one of its ancestors. That aliases a different existing
    # SQLite mailbox even though the immediate storage path is not a symlink.
    # Refuse such paths before opening/migrating any database; the active
    # native owner must supply an exact, unaliased storage directory.
    # A second hard link to the SQLite inode is another alias, even when no
    # pathname is a symlink. Its independent caller can mutate the same mailbox
    # outside this adapter's path checks. Admit only a single-link existing file.
    # _connection() chmods the selected file and may alter its schema. The
    # exact pre-existing mailbox must therefore be an owned, private regular
    # file inside an owned, private directory. Otherwise even an unaliased
    # pathname can target another actor's storage and mutate it on open.
    # This remains a preflight, not a substitute for a native inode/lease fence.
    try:
        # The parent of the private mailbox must not be replaceable by
        # another local account. A 0700 mailbox inside a group/world-writable
        # base can be renamed between preflight and SQLite's open(). This is
        # an additional admission check, not atomic inode/lease fencing.
        base_meta = storage_base.lstat()
        root_meta = storage_root.lstat()
        file_meta = storage_file.lstat()
        private_storage = (
            stat.S_ISDIR(base_meta.st_mode)
            and base_meta.st_uid == os.geteuid()
            and base_meta.st_mode & 0o022 == 0
            and stat.S_ISDIR(root_meta.st_mode)
            and root_meta.st_uid == os.geteuid()
            and root_meta.st_mode & 0o077 == 0
            and stat.S_ISREG(file_meta.st_mode)
            and file_meta.st_uid == os.geteuid()
            and file_meta.st_nlink == 1
            and file_meta.st_mode & 0o077 == 0
        )
    except OSError:
        private_storage = False
    # WAL/SHM/journal sidecars are opened by SQLite before row validation.
    # Refuse existing aliases and unsafe files before opening any mailbox.
    # This remains a preflight, not descriptor-level inode or lease fencing.
    if private_storage:
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(storage_file) + suffix)
            try:
                side_meta = sidecar.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                private_storage = False
                break
            if not (
                stat.S_ISREG(side_meta.st_mode)
                and side_meta.st_uid == os.geteuid()
                and side_meta.st_nlink == 1
                and side_meta.st_mode & 0o077 == 0
            ):
                private_storage = False
                break
    if (any(part.is_symlink() for part in (storage_base, *storage_base.parents))
            or storage_root.is_symlink() or storage_file.is_symlink()
            or not private_storage):
        raise ApplyHold("existing_sqlite_storage_required")
    # An existing private filename alone is not an existing SQLite mailbox.
    # _connection() can initialize/migrate a zero-byte or non-SQLite file;
    # refuse damaged/wrong storage without mutating it or submitting report.
    # O_NOFOLLOW plus fstat also rejects an inode swapped after lstat but
    # before the header read. This still is NOT a native atomic inode fence
    # across the later SQLite open: the existing owner must enforce that.
    try:
        # A raced-in FIFO/device must not block the worker at open().
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
        fd = os.open(storage_file, flags)
        try:
            opened = os.fstat(fd)
            header = os.read(fd, 16)
        finally:
            os.close(fd)
    except OSError as exc:
        raise ApplyHold("existing_sqlite_storage_required") from exc
    if (opened.st_dev != file_meta.st_dev
            or opened.st_ino != file_meta.st_ino
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or opened.st_mode & 0o077
            or header != b"SQLite format 3\x00"):
        raise ApplyHold("existing_sqlite_storage_required")
    from msw_engine.hermes_chat_bridge import SCHEMA, handle
    # The existing report operation owns the SQLite BEGIN IMMEDIATE transaction,
    # validates the lease, and enforces idempotency.
    with closing(_connection(directory)) as db:
        row = db.execute("SELECT id,conversation,executor,request_sha,work_key,parent_id,"
                         "state,lease_sha,lease_until,result,result_sha,outcome "
                         "FROM jobs WHERE id=?",
                         (job["task_id"],)).fetchone()
        if (row is None or row["work_key"] != job["work_key"]
                or row["parent_id"] != job["parent_id"]):
            raise ApplyHold("work_identity_mismatch")
        if (row["conversation"] != job["conversation_key"]
                or row["executor"] != job["executor"]
                or row["request_sha"] != job["request_sha256"]):
            raise ApplyHold("persisted_origin_mismatch")
        if row["lease_sha"] != lease_token_digest:
            raise ApplyHold("lease_not_valid")
        if row["state"] in ("reported", "acknowledged"):
            # A previous report may have committed before its response was lost.
            # Read the stored effect; never submit the uncertain report again.
            if (row["result"] != result or row["result_sha"] != digest
                    or row["outcome"] != "completed"):
                raise ApplyHold("conflicting_recorded_result")
            report_already_recorded = True
        elif row["state"] == "leased":
            # The existing report handler enforces lease expiry inside its
            # transaction. Detect an already-expired reservation here as well
            # so the adapter never attempts a doomed report or turns that
            # known denial into an ambiguous UNKNOWN outcome.
            # A previously committed report is reconciled above regardless
            # of the old lease deadline; never replay it.
            if (type(row["lease_until"]) is not int
                    or row["lease_until"] <= int(time.time())):
                raise ApplyHold("lease_expired")
            report_already_recorded = False
        else:
            raise ApplyHold("work_not_leased")
    if not report_already_recorded:
        # The remote reply can be lost AFTER the transaction commits. A failure
        # here is UNKNOWN, never a reason to send another report or rerun Codex.
        # A later independent call reconciles the original immutable DB row.
        try:
            report = handle(dict(schema=SCHEMA, operation="report", task_id=job["task_id"],
                                 lease_token=lease_token, result=result,
                                 result_sha256=digest, outcome="completed"), directory)
        except (Exception, CancelledError) as exc:
            # Cancellation can arrive after SQLite has committed the report.
            # Preserve UNKNOWN; do not permit an automatic report/Codex replay.
            raise ApplyHold("report_unknown_outcome") from exc
        # A transport may return None or a non-object AFTER the storage write
        # committed. Treat that as UNKNOWN, not an AttributeError that invites
        # a caller to rerun the report or Codex. Reconcile original row later.
        if (not isinstance(report, dict)
                or report.get("state") not in ("reported", "acknowledged")
                or report.get("result_sha256") != digest):
            raise ApplyHold("report_unknown_outcome")
    # This SQLite chat transport's generic "ack" operation only changes a
    # local storage row. It does NOT prove that the actual Hermes process
    # received, validated, or applied the result. Never issue that ACK for
    # Hermes from the Codex adapter or promote transport storage to native apply.
    # The authorized native owner must reconcile and ACK through its own
    # authenticated contract, independently from this method.
    with closing(_connection(directory)) as db:
        stored = db.execute("SELECT id,conversation,executor,request_sha,work_key,parent_id,"
                            "state,result,result_sha,outcome FROM jobs WHERE id=?",
                            (job["task_id"],)).fetchone()
        if (stored is None or stored["conversation"] != job["conversation_key"]
                or stored["executor"] != job["executor"]
                or stored["request_sha"] != job["request_sha256"]
                or stored["work_key"] != job["work_key"]
                or stored["parent_id"] != job["parent_id"]
                or stored["state"] not in ("reported", "acknowledged")
                or stored["result"] != result or stored["result_sha"] != digest
                or stored["outcome"] != "completed"):
            raise ApplyHold("readback_mismatch")
        storage_state = stored["state"]
    return {"status": "STORAGE_REPORT_VERIFIED_NATIVE_UNVERIFIED",
            "task_id": job["task_id"], "work_key": job["work_key"],
            "result_sha256": digest, "storage_state": storage_state,
            "storage_readback_verified": True,
            "native_hermes_ack_verified": False,
            "native_application_verified": False}
