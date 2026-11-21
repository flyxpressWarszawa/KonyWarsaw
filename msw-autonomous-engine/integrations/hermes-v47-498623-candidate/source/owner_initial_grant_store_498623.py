"""Create-only owner initial-grant readback for the existing 498623 case.

This is a grant receipt, NOT a work queue, admission, platform attestation,
ACK or permission to apply a native effect. Only the source-pinned V47 owner
may call it while holding the *existing* business lock and validating the
current admission, run, STOP/lease/source fences. The parent directory must
already exist and be owned privately by the production process.
"""
import copy
import ctypes
import errno
import json
import os
from pathlib import Path
import stat
import tempfile

import profile_498623 as p
from initial_grant_498623 import GRANT_KEYS, GRANT_SCHEMA

FILENAME = "initial-grant-498623.json"
READBACK_SCHEMA = "msw.owner-supplement.initial-grant-readback.v1"
HEX = set("0123456789abcdef")
MAX_BYTES = 16384


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8", "strict")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise p.AdmissionError("grant_store_invalid_json") from exc


def _valid_grant(grant):
    p.require(isinstance(grant, dict) and set(grant) == GRANT_KEYS
              and grant.get("schema") == GRANT_SCHEMA
              and grant.get("root_job_id") == p.ROOT
              and grant.get("task_id") == p.TASK
              and isinstance(grant.get("operation_id"), str) and bool(grant["operation_id"])
              and isinstance(grant.get("run_id"), str) and bool(grant["run_id"])
              and isinstance(grant.get("run_context"), dict)
              and isinstance(grant.get("admission_sha256"), str)
              and len(grant["admission_sha256"]) == 64
              and all(c in HEX for c in grant["admission_sha256"]),
              "grant_store_binding")
    _canonical(grant)


class OwnerInitialGrantStore:
    """Call only from the authorized owner after acquiring its business lock."""

    def __init__(self, directory):
        path = Path(directory)
        p.require(path.is_absolute() and path.is_dir()
                  and not path.is_symlink() and path.resolve(strict=True) == path,
                  "grant_store_private_directory")
        info = path.stat()
        p.require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                  and not info.st_mode & 0o077, "grant_store_directory_permissions")
        self.directory = path
        self.path = path / FILENAME

    def _directory_identity(self):
        info = self.directory.lstat()
        p.require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                  and not info.st_mode & 0o077
                  and self.directory.resolve(strict=True) == self.directory,
                  "grant_store_directory_changed")
        return (info.st_dev, info.st_ino)

    def _safe_read(self):
        p.require(self._directory_identity(), "grant_store_directory_changed")
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            before = os.fstat(fd)
            named = self.path.lstat()
            p.require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                      and before.st_nlink == 1 and not before.st_mode & 0o077
                      and before.st_size <= MAX_BYTES
                      and (before.st_dev, before.st_ino) == (named.st_dev, named.st_ino),
                      "grant_store_file_identity")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(MAX_BYTES + 1)
            after = os.fstat(fd)
            p.require(len(raw) == before.st_size and len(raw) <= MAX_BYTES
                      and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                      == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                      and self._directory_identity(), "grant_store_file_changed")
            return raw
        finally:
            os.close(fd)

    def read_498623_initial_grant(self, *, root_job_id, task_id):
        p.require(root_job_id == p.ROOT and task_id == p.TASK,
                  "grant_store_wrong_target")
        raw = self._safe_read()
        try:
            value = p.strict_json(raw.decode("utf-8", "strict"))
        except UnicodeError as exc:
            raise p.AdmissionError("grant_store_invalid_utf8") from exc
        p.require(isinstance(value, dict)
                  and set(value) == {"schema", "grant", "grant_sha256", "admission_sha256",
                                     "operation_id", "current_run_id", "current_run_context"}
                  and value.get("schema") == READBACK_SCHEMA
                  and isinstance(value.get("grant"), dict),
                  "grant_store_readback_shape")
        grant = value["grant"]
        _valid_grant(grant)
        p.require(value["grant_sha256"] == p.digest(grant)
                  and value["admission_sha256"] == grant["admission_sha256"]
                  and value["operation_id"] == grant["operation_id"]
                  and value["current_run_id"] == grant["run_id"]
                  and p.digest(value["current_run_context"]) == p.digest(grant["run_context"])
                  and _canonical(value) == raw,
                  "grant_store_readback_integrity")
        return copy.deepcopy(value)

    def append_498623_initial_grant(self, grant):
        _valid_grant(grant)
        grant = copy.deepcopy(grant)
        result = {
            "schema": READBACK_SCHEMA,
            "grant": grant,
            "grant_sha256": p.digest(grant),
            "admission_sha256": grant["admission_sha256"],
            "operation_id": grant["operation_id"],
            "current_run_id": grant["run_id"],
            "current_run_context": copy.deepcopy(grant["run_context"]),
        }
        raw = _canonical(result)
        p.require(len(raw) <= MAX_BYTES, "grant_store_size")
        identity = self._directory_identity()
        fd, temporary = tempfile.mkstemp(dir=self.directory, prefix=".grant-stage-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            p.require(self._directory_identity() == identity, "grant_store_directory_changed")
            # Linux RENAME_NOREPLACE: atomic complete-file publication
            # with no interval where the visible receipt has two hardlinks.
            # Do not fall back to replace/overwrite on other kernels.
            libc = ctypes.CDLL(None, use_errno=True)
            rename = libc.renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p,
                               ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            rc = rename(-100, os.fsencode(temporary), -100, os.fsencode(self.path), 1)
            if rc != 0:
                p.require(ctypes.get_errno() == errno.EEXIST,
                          "grant_store_atomic_publish_unavailable")
                p.require(p.digest(self.read_498623_initial_grant(
                    root_job_id=p.ROOT, task_id=p.TASK)) == p.digest(result),
                    "grant_store_conflicting_run")
            else:
                directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        p.require(p.digest(self.read_498623_initial_grant(root_job_id=p.ROOT, task_id=p.TASK))
                  == p.digest(result), "grant_store_readback_mismatch")
        return result
