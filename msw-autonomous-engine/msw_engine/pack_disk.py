"""Store approved private deployment archives on the existing Render persistent disk.

Only fixed filenames derived from a pinned SHA-256; never user/job-selected paths.
No object store, network, subprocess, environment-value logging or public files.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

MAX_BYTES = 4 * 1024 * 1024


class PrivateArchiveError(ValueError):
    pass


def _archive_path(data_dir: Path, digest: str) -> Path:
    if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
        raise PrivateArchiveError("private_archive_pin_invalid")
    if data_dir.is_symlink():
        raise PrivateArchiveError("private_archive_root_symlink")
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive_dir = data_dir / "private-pack-archives"
    if archive_dir.is_symlink():
        raise PrivateArchiveError("private_archive_dir_symlink")
    archive_dir.mkdir(mode=0o700, exist_ok=True)
    if not archive_dir.is_dir() or archive_dir.is_symlink():
        raise PrivateArchiveError("private_archive_dir_invalid")
    archive_dir.chmod(0o700)
    return archive_dir / (digest + ".zip")


def restore_archive(data_dir: Path, digest: str) -> bytes:
    path = _archive_path(data_dir, digest)
    if path.is_symlink() or not path.is_file():
        raise PrivateArchiveError("private_archive_unavailable")
    metadata = path.stat()
    if metadata.st_mode & 0o077 or not stat.S_ISREG(metadata.st_mode):
        raise PrivateArchiveError("private_archive_permissions")
    if metadata.st_size <= 0 or metadata.st_size > MAX_BYTES:
        raise PrivateArchiveError("private_archive_size")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise PrivateArchiveError("private_archive_digest_mismatch")
    return raw


def persist_archive(data_dir: Path, raw: bytes, digest: str) -> None:
    path = _archive_path(data_dir, digest)
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise PrivateArchiveError("private_archive_size")
    if hashlib.sha256(raw).hexdigest() != digest:
        raise PrivateArchiveError("private_archive_digest_mismatch")
    if path.exists() or path.is_symlink():
        if restore_archive(data_dir, digest) != raw:
            raise PrivateArchiveError("private_archive_conflict")
        return
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        fd = -1
        try:
            # Atomic, no-clobber publication. Concurrent writers can only win
            # with exactly the same pinned content.
            os.link(temporary, path)
        except FileExistsError:
            pass
        if restore_archive(data_dir, digest) != raw:
            raise PrivateArchiveError("private_archive_readback_failed")
    finally:
        if fd >= 0:
            os.close(fd)
        if os.path.exists(temporary):
            os.unlink(temporary)
