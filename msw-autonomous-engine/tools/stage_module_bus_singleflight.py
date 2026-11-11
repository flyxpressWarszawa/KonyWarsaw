"""Stage-only, exact-anchor guard for MSW WordPress module bus.

NEVER writes to a live WordPress path. The current WordPress PHP source is NOT
stored in this GitHub tree, and the WPVibe source viewer may redact secrets.
An authorized server-side, byte-exact SHA preflight and independent restore
are REQUIRED before any future production installation.

This tool only creates a private backup + candidate in a separate directory.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

MARKER = "MSW_MODULE_BUS_SINGLEFLIGHT_20261111"
ORIGINAL = r"""        $all=self::catalog();$slot=gmdate('YmdH');$key='module-bus-sync-v1-'.$all['catalog_sha256'].'-'.$slot;
        $job=\MSW\UnifiedEngine\RemoteStore::enqueue('runtime.catalog',['module_bus'=>['operation'=>'sync','catalog_sha256'=>$all['catalog_sha256']]],$key);"""

REPLACEMENT = r"""        $all=self::catalog();
        // MSW_MODULE_BUS_SINGLEFLIGHT_20261111
        // A previously admitted snapshot must be authoritatively reconciled.
        // Do not create a second job for the SAME topology if the prior job
        // is pending/running, terminal, uncertain, absent or identity-conflicted.
        $prior=(array)($s['last_queued']??[]);
        if (($prior['catalog_sha256']??null)===$all['catalog_sha256']) {
            if (!is_string($prior['job_id']??null)
                || preg_match('/^[a-f0-9-]{36}$/D',$prior['job_id'])!==1)
                return self::error('singleflight_job_id_unverified',503);
            global $wpdb;
            $table=\MSW\UnifiedEngine\RemoteStore::table();
            $existing=$wpdb->get_row($wpdb->prepare("SELECT kind,state,payload_sha256 FROM {$table} WHERE id=%s",$prior['job_id']),ARRAY_A);
            $raw=self::json(['module_bus'=>['operation'=>'sync','catalog_sha256'=>$all['catalog_sha256']]]);
            if (!is_array($existing) || ($existing['kind']??null)!=='runtime.catalog'
                || !hash_equals(hash('sha256',$raw),(string)($existing['payload_sha256']??'')))
                return self::error('singleflight_existing_job_unverified',503);
            if (!in_array($existing['state']??null,['pending','running'],true))
                return self::error('singleflight_terminal_review',409);
            $reused=false;$checkedAt=time();
            $saved=self::mutate(static function(&$next)use($prior,$all,$checkedAt,&$reused){
                if (($next['last_queued']['job_id']??null)!==$prior['job_id']
                    || ($next['last_queued']['catalog_sha256']??null)!==$all['catalog_sha256'])return;
                $next['last_checked']=$checkedAt;
                if (($next['last_sync']['job_id']??null)!==$prior['job_id'])
                    $next['pending_receipts'][$prior['job_id']]=['kind'=>'runtime.catalog'];
                $reused=true;
            });
            if (!$saved || !$reused)return self::error('singleflight_state_uncertain',503);
            // WordPress update_option can fail even after the advisory lock succeeds.
            // Read back the exact identity, receipt and timestamp before ACK.
            $read=self::state();
            if (($read['last_queued']['job_id']??null)!==$prior['job_id']
                || ($read['last_queued']['catalog_sha256']??null)!==$all['catalog_sha256']
                || (int)($read['last_checked']??0)<$checkedAt
                || (($read['last_sync']['job_id']??null)!==$prior['job_id']
                    && ($read['pending_receipts'][$prior['job_id']]['kind']??null)!=='runtime.catalog'))
                return self::error('singleflight_readback_unverified',503);
            return $prior+['queued'=>true,'completed'=>false,'reused_pending'=>true,'checked_at'=>gmdate('c',$checkedAt)];
        }
        $slot=gmdate('YmdH');$key='module-bus-sync-v1-'.$all['catalog_sha256'].'-'.$slot;
        $job=\MSW\UnifiedEngine\RemoteStore::enqueue('runtime.catalog',['module_bus'=>['operation'=>'sync','catalog_sha256'=>$all['catalog_sha256']]],$key);"""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_candidate(data: bytes, *, expected_sha256: str) -> bytes:
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise ValueError("expected_sha_invalid")
    # Explicit digest check, no secret-bearing source printed.
    import hmac
    if not hmac.compare_digest(digest(data), expected_sha256):
        raise ValueError("source_digest_changed")
    if len(data) > 1024 * 1024 or b"\0" in data:
        raise ValueError("source_size_or_encoding")
    try:
        source = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError("source_encoding") from e
    if MARKER in source:
        raise ValueError("already_modified")
    if "final class MSW_Federation_Module_Bus" not in source:
        raise ValueError("unrecognized_plugin_source")
    if source.count(ORIGINAL) != 1:
        raise ValueError("source_anchor_not_unique")
    return source.replace(ORIGINAL, REPLACEMENT, 1).encode("utf-8")


def write_exclusive(path: Path, data: bytes) -> None:
    # Staging-only, atomic no-clobber creation; no symlinks.
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def stage_source(source: Path, staging_dir: Path, expected_sha256: str) -> dict:
    if source.is_symlink() or not source.is_file():
        raise ValueError("source_not_regular")
    if not staging_dir.is_dir() or staging_dir.is_symlink():
        raise ValueError("staging_dir_unavailable")
    if staging_dir.resolve() == source.parent.resolve():
        raise ValueError("staging_must_be_separate")
    if stat.S_IMODE(staging_dir.stat().st_mode) & 0o077:
        raise ValueError("staging_permissions_too_broad")
    original = source.read_bytes()
    updated = make_candidate(original, expected_sha256=expected_sha256)
    backup = staging_dir / "module-bus-before.php"
    candidate = staging_dir / "module-bus-candidate.php"
    if backup.exists() or backup.is_symlink() or candidate.exists() or candidate.is_symlink():
        raise ValueError("staging_path_already_exists")
    write_exclusive(backup, original)
    try:
        write_exclusive(candidate, updated)
        if digest(backup.read_bytes()) != digest(original) or digest(candidate.read_bytes()) != digest(updated):
            raise ValueError("staging_readback_mismatch")
    except BaseException:
        candidate.unlink(missing_ok=True)
        # Keep backup after error; no automatic source mutation ever occurred.
        raise
    return {"schema": "msw.module-bus.stage.v1", "state": "STAGED_NOT_DEPLOYED",
            "before_sha256": digest(original), "candidate_sha256": digest(updated),
            "backup_readback_verified": True, "candidate_readback_verified": True,
            "production_changed": False, "new_jobs": 0}


def restore_staged(candidate: Path, backup: Path, *, expected_candidate_sha256: str) -> dict:
    """Local rollback demonstration only. Never writes back to the source."""
    import hmac
    if not re.fullmatch(r"[a-f0-9]{64}", expected_candidate_sha256):
        raise ValueError("candidate_digest_invalid")
    if candidate.is_symlink() or backup.is_symlink() or not candidate.is_file() or not backup.is_file():
        raise ValueError("staging_files_invalid")
    if not hmac.compare_digest(digest(candidate.read_bytes()), expected_candidate_sha256):
        raise ValueError("candidate_changed")
    if candidate.resolve().parent != backup.resolve().parent:
        raise ValueError("staging_location_mismatch")
    before = backup.read_bytes()
    # Staging rollback is explicit, not a production restore procedure.
    candidate.write_bytes(before)
    os.chmod(candidate, 0o600)
    if digest(candidate.read_bytes()) != digest(before):
        raise ValueError("rollback_readback_failed")
    return {"state": "STAGING_ROLLBACK_VERIFIED", "restored_sha256": digest(before),
            "production_changed": False}


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("Usage: python stage_module_bus_singleflight.py SOURCE STAGING_DIR EXACT_SOURCE_SHA256")
    try:
        output = stage_source(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    except (OSError, ValueError) as e:
        print(json.dumps({"state": "HOLD", "code": str(e)[:80], "production_changed": False}))
        raise SystemExit(2) from None
    print(json.dumps(output, sort_keys=True))
