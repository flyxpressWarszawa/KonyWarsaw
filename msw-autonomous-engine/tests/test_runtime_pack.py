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

from msw_engine.runtime_pack import MANIFEST, PackError, install, unpack, module_root


def make_pack(*, name="example/code.py", body=b"VALUE = 42\n", manifest_override=None, extras=(), symlink=False):
    metadata = {"schema": "msw.runtime-pack.v1", "modules": [{"id": "example", "version": "1.0.0",
                "root": "example", "source_sha256": "a" * 64}], "files": {name: hashlib.sha256(body).hexdigest()}}
    if manifest_override is not None:
        metadata = manifest_override
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo(name)
        info.create_system = 3
        info.external_attr = ((stat.S_IFLNK | 0o777) if symlink else (stat.S_IFREG | 0o600)) << 16
        archive.writestr(info, body)
        archive.writestr(MANIFEST, json.dumps(metadata))
        for path, value in extras:
            archive.writestr(path, value)
    raw = stream.getvalue()
    return raw, hashlib.sha256(raw).hexdigest()


class RuntimePackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.previous_root = os.environ.pop("MSW_RUNTIME_PACK_ROOT", None)

    def tearDown(self):
        os.environ.pop("MSW_RUNTIME_PACK_ROOT", None)
        if self.previous_root is not None:
            os.environ["MSW_RUNTIME_PACK_ROOT"] = self.previous_root
        self.temp.cleanup()

    def test_installs_verified_private_files_and_is_idempotent(self):
        raw, digest = make_pack()
        root, manifest = unpack(raw, digest, self.directory)
        self.assertEqual((root / "example/code.py").read_bytes(), b"VALUE = 42\n")
        self.assertEqual((root / "example/code.py").stat().st_mode & 0o777, 0o600)
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(unpack(raw, digest, self.directory)[0], root)

    def test_rejects_archive_digest_mismatch(self):
        raw, _ = make_pack()
        with self.assertRaisesRegex(PackError, "digest_mismatch"):
            unpack(raw, "0" * 64, self.directory)

    def test_rejects_traversal_absolute_and_windows_paths(self):
        for name in (".", "../code.py", "/code.py", "example/../code.py", "C:/code.py", "example\\code.py"):
            with self.subTest(name=name):
                raw, digest = make_pack(name=name)
                with self.assertRaises(PackError):
                    unpack(raw, digest, self.directory)
        self.assertFalse((self.directory.parent / "code.py").exists())

    def test_rejects_symlink(self):
        raw, digest = make_pack(symlink=True)
        with self.assertRaisesRegex(PackError, "links"):
            unpack(raw, digest, self.directory)

    def test_rejects_unlisted_and_duplicate_files(self):
        for extras in ((('example/extra.py', 'x'),), (('example/code.py', 'other'),)):
            with self.subTest(extras=extras):
                raw, digest = make_pack(extras=extras)
                with self.assertRaises(PackError):
                    unpack(raw, digest, self.directory)

    def test_rejects_invalid_manifest_shape(self):
        for metadata in ([], {"schema": "unknown"}, {"schema": "msw.runtime-pack.v1", "files": {}, "modules": []}):
            raw, digest = make_pack(manifest_override=metadata)
            with self.assertRaises(PackError):
                unpack(raw, digest, self.directory)

    def test_detects_modified_cached_code(self):
        raw, digest = make_pack()
        root, _ = unpack(raw, digest, self.directory)
        (root / "example/code.py").write_text("tampered")
        with self.assertRaisesRegex(PackError, "file_mismatch"):
            unpack(raw, digest, self.directory)

    def test_cached_parent_symlink_is_rejected_even_if_file_digest_matches(self):
        raw, digest = make_pack()
        root, _ = unpack(raw, digest, self.directory)
        outside = self.directory / "outside"
        outside.mkdir()
        (outside / "code.py").write_bytes(b"VALUE = 42\n")
        (root / "example/code.py").unlink()
        (root / "example").rmdir()
        (root / "example").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(PackError, "file_mismatch"):
            unpack(raw, digest, self.directory)

    def test_cached_runtime_pack_directory_symlink_is_rejected(self):
        raw, digest = make_pack()
        outside = self.directory / "outside"
        outside.mkdir()
        (self.directory / "runtime-packs").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(PackError, "cache_symlink"):
            unpack(raw, digest, self.directory)

    def test_detects_modified_cached_manifest(self):
        raw, digest = make_pack()
        root, _ = unpack(raw, digest, self.directory)
        (root / MANIFEST).write_text("{}")
        with self.assertRaisesRegex(PackError, "manifest_mismatch"):
            unpack(raw, digest, self.directory)

    def test_configuration_install_sets_only_verified_module_root(self):
        raw, digest = make_pack()
        value = install(self.directory, {"MSW_RUNTIME_PACK_CHUNKS": "1", "MSW_RUNTIME_PACK_00": base64.b64encode(raw).decode(),
                                         "MSW_RUNTIME_PACK_SHA256": digest})
        self.assertEqual(value["state"], "installed_verified")
        self.assertIsNotNone(module_root("example"))
        self.assertIsNone(module_root("not-installed"))

    def test_invalid_pack_keeps_base_worker_available_and_disables_new_module(self):
        os.environ["MSW_RUNTIME_PACK_ROOT"] = "/unverified"
        value = install(self.directory, {"MSW_RUNTIME_PACK_CHUNKS": "1", "MSW_RUNTIME_PACK_00": "not base64"})
        self.assertEqual(value["state"], "blocked")
        self.assertNotIn("MSW_RUNTIME_PACK_ROOT", os.environ)
        from msw_engine.tasks import handlers
        self.assertIn("seo.analyze", handlers())
        self.assertNotIn("supreme.observe", handlers())

    def test_no_private_pack_does_not_change_base_job_handlers(self):
        value = install(self.directory, {})
        self.assertEqual(value["state"], "not_configured")
        from msw_engine.tasks import handlers
        self.assertEqual(set(handlers()), {"seo.analyze", "knowledge.ingest", "knowledge.search", "research.rank", "runtime.catalog"})


if __name__ == "__main__":
    unittest.main()
