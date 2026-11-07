import hashlib
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from msw_engine.doctor_pack_integrity import PackIntegrity
from msw_engine.runtime_pack import MANIFEST, unpack
from test_runtime_pack import make_pack


class Clock:
    now = 0
    def __call__(self):
        return self.now


class PackIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        raw, digest = make_pack()
        self.root, self.manifest = unpack(raw, digest, Path(self.temp.name))
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.manifest_digest = hashlib.sha256(archive.read(MANIFEST)).hexdigest()
        self.env = {'MSW_RUNTIME_PACK_ROOT': str(self.root)}
        self.clock = Clock()
        self.guard = PackIntegrity(self.root, self.manifest['files'], self.manifest_digest,
                                   env=self.env, clock=self.clock, monotonic=self.clock, interval=900)

    def test_tampered_code_blocks_future_work_and_stays_blocked(self):
        self.assertEqual(self.guard.check()['state'], 'verified')
        (self.root / 'example/code.py').write_text('tampered')
        self.clock.now = 899
        self.assertEqual(self.guard.check()['state'], 'verified')
        self.clock.now = 900
        status = self.guard.check()
        self.assertEqual(status['state'], 'integrity_blocked')
        self.assertTrue(status['private_runtime_disabled'])
        self.assertNotIn('MSW_RUNTIME_PACK_ROOT', self.env)
        self.assertNotIn('example', str(status))
        (self.root / 'example/code.py').write_bytes(b'VALUE = 42\n')
        self.clock.now = 1800
        self.assertTrue(self.guard.blocked)
        self.assertEqual(self.guard.check()['state'], 'integrity_blocked')

    def test_changed_manifest_is_blocked_even_if_files_are_unchanged(self):
        (self.root / MANIFEST).write_text('{}')
        self.assertEqual(self.guard.check()['state'], 'integrity_blocked')

    def test_does_not_remove_a_different_runtime_root(self):
        self.env['MSW_RUNTIME_PACK_ROOT'] = '/different/verified/root'
        (self.root / 'example/code.py').write_text('tampered')
        status = self.guard.check()
        self.assertEqual(status['state'], 'integrity_blocked')
        self.assertFalse(status['private_runtime_disabled'])
        self.assertEqual(self.env['MSW_RUNTIME_PACK_ROOT'], '/different/verified/root')
