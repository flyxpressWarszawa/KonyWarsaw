"""Install an operator-approved private runtime pack without publishing its source.

Only deployment configuration can select a pack. Jobs cannot supply code, paths,
commands or archives. The normal queue remains available if a new pack is invalid.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile

from .pack_disk import PrivateArchiveError, persist_archive, restore_archive

MAX_ENCODED = 1_000_000
MAX_EXPANDED = 32 * 1024 * 1024
MANIFEST = "msw-runtime-pack.json"


class PackError(ValueError):
    pass


def safe_name(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value and len(value) <= 400 and not path.is_absolute()
                and ".." not in path.parts and "\\" not in value
                and not re.search(r"[\x00-\x1f:]", value)
                and path.as_posix() == value)


def verify_directory(root: Path, files: dict[str, str]) -> None:
    for name, digest in files.items():
        if not safe_name(name) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise PackError("invalid_file_manifest")
        path = root / name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise PackError("private_pack_file_mismatch")


def unpack(raw: bytes, digest: str, data_dir: Path) -> tuple[Path, dict]:
    if not re.fullmatch(r"[a-f0-9]{64}", digest) or hashlib.sha256(raw).hexdigest() != digest:
        raise PackError("private_pack_digest_mismatch")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        infos = archive.infolist()
        names = [entry.filename for entry in infos]
        if len(infos) > 3000 or len(names) != len(set(names)) or MANIFEST not in names:
            raise PackError("private_pack_entry_limit")
        if any(not safe_name(name) for name in names):
            raise PackError("private_pack_unsafe_path")
        if any(stat.S_ISLNK(entry.external_attr >> 16) or entry.is_dir() for entry in infos):
            raise PackError("private_pack_links_or_directories")
        if sum(entry.file_size for entry in infos) > MAX_EXPANDED:
            raise PackError("private_pack_expanded_limit")
        manifest_raw = archive.read(MANIFEST)
        manifest = json.loads(manifest_raw)
        if not isinstance(manifest, dict):
            raise PackError("private_pack_manifest_invalid")
        files = manifest.get("files")
        modules = manifest.get("modules")
        if manifest.get("schema") != "msw.runtime-pack.v1" or not isinstance(files, dict) or not isinstance(modules, list):
            raise PackError("private_pack_manifest_invalid")
        if set(files) != set(names) - {MANIFEST}:
            raise PackError("private_pack_unlisted_file")
        if len(modules) > 200 or len({item.get("id") for item in modules if isinstance(item, dict)}) != len(modules):
            raise PackError("private_pack_module_invalid")
        for module in modules:
            if not isinstance(module, dict) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", module.get("id", "")):
                raise PackError("private_pack_module_invalid")
            if not safe_name(module.get("root", "")) or not re.fullmatch(r"[a-f0-9]{64}", module.get("source_sha256", "")):
                raise PackError("private_pack_module_invalid")
        cache = data_dir / "runtime-packs"
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = cache / digest
        if target.is_symlink():
            raise PackError("private_pack_cache_symlink")
        if target.exists():
            if (target / MANIFEST).is_symlink() or (target / MANIFEST).read_bytes() != manifest_raw:
                raise PackError("private_pack_manifest_mismatch")
            verify_directory(target, files)
            return target, manifest
        temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=cache))
        try:
            for entry in infos:
                target_file = temporary / entry.filename
                target_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                target_file.write_bytes(archive.read(entry))
                target_file.chmod(0o600)
            verify_directory(temporary, files)
            temporary.rename(target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return target, manifest
    except (zipfile.BadZipFile, KeyError, TypeError, json.JSONDecodeError) as error:
        raise PackError("private_pack_invalid_archive") from error


def install(data_dir: Path, environ=None) -> dict:
    environ = os.environ if environ is None else environ
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ.pop("MSW_RUNTIME_PACK_ROOT", None)
    value = {"schema": "msw.runtime-status.v1", "modules": [], "state": "not_configured"}
    try:
        count = int(environ.get("MSW_RUNTIME_PACK_CHUNKS", "0"))
        digest = environ.get("MSW_RUNTIME_PACK_SHA256", "")
        if not count and not digest:
            return value
        if not 0 <= count <= 32:
            raise PackError("private_pack_chunk_limit")
        if count:
            encoded = "".join(environ.get(f"MSW_RUNTIME_PACK_{i:02d}", "") for i in range(count))
            if not encoded or len(encoded) > MAX_ENCODED:
                raise PackError("private_pack_encoded_limit")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as error:
                raise PackError("private_pack_encoding") from error
            source = "environment_migrated_to_disk"
        else:
            raw = restore_archive(data_dir, digest)
            source = "verified_private_disk"
        root, manifest = unpack(raw, digest, data_dir)
        archived = not count
        if count:
            # A disk-backup problem MUST NOT disable the already verified live
            # env-based pack. It blocks env cleanup, not useful existing work.
            try:
                persist_archive(data_dir, raw, digest)
                archived = True
            except (PrivateArchiveError, OSError):
                archived = False
        value.update(state="installed_verified", pack_sha256=digest, modules=manifest["modules"],
                     source=source, archive_readback_verified=archived)
        os.environ["MSW_RUNTIME_PACK_ROOT"] = str(root)
    except (PackError, OSError, ValueError) as error:
        value.update(state="blocked", error=str(error) if isinstance(error, (PackError, PrivateArchiveError)) else "private_pack_configuration")
    finally:
        temporary = data_dir / ".runtime-status.tmp"
        temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        temporary.chmod(0o600)
        temporary.replace(data_dir / "runtime-status.json")
    return value


def module_root(module_id: str) -> Path | None:
    root = os.environ.get("MSW_RUNTIME_PACK_ROOT", "")
    if not root:
        return None
    try:
        base = Path(root)
        manifest = json.loads((base / MANIFEST).read_text())
        for module in manifest["modules"]:
            if module["id"] == module_id:
                if not safe_name(module["root"]):
                    return None
                result = base / module["root"]
                return result if result.is_dir() and not result.is_symlink() and result.resolve().is_relative_to(base.resolve()) else None
    except (OSError, ValueError, KeyError):
        return None
    return None
