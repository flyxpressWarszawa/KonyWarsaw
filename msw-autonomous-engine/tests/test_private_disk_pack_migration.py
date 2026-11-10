"""Purely synthetic private-pack migration acceptance tests; no Render/WordPress calls."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from msw_engine.pack_disk import PrivateArchiveError, persist_archive, restore_archive
from msw_engine.runtime_pack import MANIFEST, install as install_runtime, module_root
from msw_engine.own_model import install as install_model
from msw_engine.business_knowledge import seed as seed_business


def zip_pack(files, *, module_id, module_root_name):
    manifest = {
        "schema": "msw.runtime-pack.v1",
        "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()},
        "modules": [{"id": module_id, "root": module_root_name, "source_sha256": "a" * 64}],
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
        archive.writestr(MANIFEST, json.dumps(manifest))
    raw = stream.getvalue()
    return raw, hashlib.sha256(raw).hexdigest()


def env_chunks(prefix, raw, sha):
    encoded = base64.b64encode(raw).decode()
    parts = [encoded[i:i+150] for i in range(0, len(encoded), 150)]
    result = {prefix + "_CHUNKS": str(len(parts)), prefix + "_SHA256": sha}
    result.update({f"{prefix}_{i:02d}": x for i, x in enumerate(parts)})
    return result


class DiskPackMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_runtime_env_to_disk_then_chunks_removed(self):
        raw, sha = zip_pack({"example/code.py": b"TEST = 42\n"},
                            module_id="example", module_root_name="example")
        with patch.dict(os.environ, {}, clear=False):
            initial = install_runtime(self.dir, env_chunks("MSW_RUNTIME_PACK", raw, sha))
            self.assertEqual(initial["state"], "installed_verified")
            self.assertEqual(initial["source"], "environment_migrated_to_disk")
            self.assertTrue(initial["archive_readback_verified"])
            self.assertIsNotNone(module_root("example"))
            recovered = install_runtime(self.dir, {"MSW_RUNTIME_PACK_CHUNKS": "0", "MSW_RUNTIME_PACK_SHA256": sha})
            self.assertEqual(recovered["state"], "installed_verified")
            self.assertEqual(recovered["source"], "verified_private_disk")
            self.assertIsNotNone(module_root("example"))
        self.assertEqual((self.dir/"private-pack-archives"/(sha+".zip")).stat().st_mode & 0o777, 0o600)

    def test_checksum_mismatch_fails_closed(self):
        raw, sha = zip_pack({"example/code.py": b"test"}, module_id="example", module_root_name="example")
        persist_archive(self.dir, raw, sha)
        archive = self.dir/"private-pack-archives"/(sha+".zip")
        archive.write_bytes(b"TAMPERED")
        with patch.dict(os.environ, {}, clear=False):
            outcome = install_runtime(self.dir, {"MSW_RUNTIME_PACK_CHUNKS": "0", "MSW_RUNTIME_PACK_SHA256": sha})
            self.assertEqual(outcome["state"], "blocked")
            self.assertNotIn("MSW_RUNTIME_PACK_ROOT", os.environ)

    def test_missing_archive_with_pin_is_blocked_not_ignored(self):
        with patch.dict(os.environ, {}, clear=False):
            result = install_runtime(self.dir, {"MSW_RUNTIME_PACK_CHUNKS": "0", "MSW_RUNTIME_PACK_SHA256": "a"*64})
            self.assertEqual(result["state"], "blocked")
            self.assertNotIn("MSW_RUNTIME_PACK_ROOT", os.environ)

    def test_no_pin_no_chunks_remains_unconfigured(self):
        with patch.dict(os.environ, {}, clear=False):
            self.assertEqual(install_runtime(self.dir, {})["state"], "not_configured")
            self.assertEqual(install_model(self.dir, {})["state"], "not_configured")
            self.assertEqual(seed_business(self.dir)["state"], "not_configured")

    def test_refuse_archive_symlink_and_bad_permissions(self):
        raw, sha = zip_pack({"example/code.py": b"x"}, module_id="example", module_root_name="example")
        persist_archive(self.dir, raw, sha)
        archive = self.dir/"private-pack-archives"/(sha+".zip")
        archive.chmod(0o644)
        with self.assertRaises(PrivateArchiveError):
            restore_archive(self.dir, sha)
        archive.chmod(0o600)
        target = self.dir/"target.zip"
        target.write_bytes(raw)
        archive.unlink()
        archive.symlink_to(target)
        with self.assertRaises(PrivateArchiveError):
            restore_archive(self.dir, sha)

    def test_reject_conflicting_archive_without_overwrite(self):
        raw, sha = zip_pack({"example/code.py": b"before"}, module_id="example", module_root_name="example")
        persist_archive(self.dir, raw, sha)
        with self.assertRaises(PrivateArchiveError):
            persist_archive(self.dir, b"after", sha)
        self.assertEqual(restore_archive(self.dir, sha), raw)

    def test_own_model_two_stage_recovery(self):
        files = {
            "msw-own-ai/model.py": b"class Test: pass\n",
            "msw-own-ai/checkpoint/checkpoint-manifest.json": b"{}",
            "msw-own-ai/checkpoint/train-corpus.txt": b"synthetic only",
            "msw-own-ai/checkpoint/validation-corpus.txt": b"synthetic only",
        }
        raw, sha = zip_pack(files, module_id="msw-own-ai", module_root_name="msw-own-ai")
        with patch.dict(os.environ, {}, clear=False):
            first = install_model(self.dir, env_chunks("MSW_OWN_MODEL_PACK", raw, sha))
            self.assertEqual(first["state"], "installed_verified")
            self.assertEqual(first["source"], "environment_migrated_to_disk")
            second = install_model(self.dir, {"MSW_OWN_MODEL_PACK_CHUNKS": "0",
                                                  "MSW_OWN_MODEL_PACK_SHA256": sha})
            self.assertEqual(second["state"], "installed_verified")
            self.assertEqual(second["source"], "verified_private_disk")
            self.assertTrue(second["archive_readback_verified"])

    def test_business_knowledge_seed_then_disk_fallback(self):
        corpus = {"schema": "msw.business-knowledge.v1", "records": [{"id":"synthetic","approved":True,"title":"Test","text":"Synthetic only"}]}
        raw = json.dumps(corpus, ensure_ascii=False, separators=(",", ":"))
        sha = hashlib.sha256(raw.encode()).hexdigest()
        env = env_chunks("MSW_BUSINESS_KNOWLEDGE", raw.encode(), sha)
        # Unlike ZIP packs, original business knowledge is raw JSON, not base64.
        chunks = [raw[i:i+100] for i in range(0, len(raw), 100)]
        env = {"MSW_BUSINESS_KNOWLEDGE_CHUNKS": str(len(chunks)), "MSW_BUSINESS_KNOWLEDGE_SHA256": sha}
        env.update({f"MSW_BUSINESS_KNOWLEDGE_{i:02d}": chunk for i,chunk in enumerate(chunks)})
        with patch("msw_engine.tasks.knowledge_ingest", return_value={"readback_verified_records": 1}):
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(seed_business(self.dir)["source"], "environment_migrated_to_disk")
            with patch.dict(os.environ, {"MSW_BUSINESS_KNOWLEDGE_SHA256": sha}, clear=True):
                restored = seed_business(self.dir)
                self.assertEqual(restored["state"], "persisted_verified")
                self.assertEqual(restored["source"], "verified_private_disk")
                self.assertEqual(restored["records"], 1)

    def test_business_missing_disk_with_sha_fails_closed_without_agent_crash(self):
        with patch.dict(os.environ, {"MSW_BUSINESS_KNOWLEDGE_SHA256": "e"*64}, clear=True):
            result = seed_business(self.dir)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["error"], "business_knowledge_disk_missing")

    def test_business_malformed_disk_pin_preserves_core_agent_startup(self):
        with patch.dict(os.environ, {"MSW_BUSINESS_KNOWLEDGE_SHA256": "not-a-sha"}, clear=True):
            result = seed_business(self.dir)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["error"], "business_knowledge_pin_invalid")

    def test_cache_failure_does_not_disable_valid_live_env_pack(self):
        raw, sha = zip_pack({"example/code.py": b"stable"},
                            module_id="example", module_root_name="example")
        with patch("msw_engine.runtime_pack.persist_archive",
                   side_effect=PrivateArchiveError("simulated_io_failure")):
            with patch.dict(os.environ, {}, clear=False):
                result = install_runtime(self.dir, env_chunks("MSW_RUNTIME_PACK", raw, sha))
                self.assertEqual(result["state"], "installed_verified")
                self.assertFalse(result["archive_readback_verified"])
                self.assertIsNotNone(module_root("example"))

    def test_model_cache_failure_does_not_disable_valid_live_model(self):
        files = {
            "msw-own-ai/model.py": b"MODEL = True",
            "msw-own-ai/checkpoint/checkpoint-manifest.json": b"{}",
            "msw-own-ai/checkpoint/train-corpus.txt": b"synthetic",
            "msw-own-ai/checkpoint/validation-corpus.txt": b"synthetic",
        }
        raw, sha = zip_pack(files, module_id="msw-own-ai",
                            module_root_name="msw-own-ai")
        with patch("msw_engine.own_model.persist_archive",
                   side_effect=PrivateArchiveError("simulated_io_failure")):
            with patch.dict(os.environ, {}, clear=False):
                result = install_model(self.dir, env_chunks("MSW_OWN_MODEL_PACK", raw, sha))
                self.assertEqual(result["state"], "installed_verified")
                self.assertFalse(result["archive_readback_verified"])
                self.assertEqual(result["pack_sha256"], sha)

    def test_disk_does_not_allow_caller_chosen_path(self):
        with self.assertRaises(PrivateArchiveError):
            restore_archive(self.dir, "../" + "x"*61)
        with self.assertRaises(PrivateArchiveError):
            restore_archive(self.dir, "fake")
