import importlib.util
import base64
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from msw_engine.doctor_sidecar import enable_verified_pack, publish_evidence, safe_observation
from msw_engine.render_doctor import RenderDoctor, status
from msw_engine.tasks import knowledge_search


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('stage_render_doctor', ROOT / 'tools' / 'stage_render_doctor.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class Probe:
    def sample(self):
        return {'cpu_percent': 95, 'memory_percent': 10}


class DoctorSidecarTests(unittest.TestCase):
    def test_private_pack_is_verified_without_overwriting_worker_status(self):
        from test_runtime_pack import make_pack
        raw, digest = make_pack()
        configured = {'MSW_RUNTIME_PACK_CHUNKS':'1', 'MSW_RUNTIME_PACK_00':base64.b64encode(raw).decode(),
                      'MSW_RUNTIME_PACK_SHA256':digest}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, configured, clear=True):
            root = Path(tmp)
            (root / 'runtime-status.json').write_text('worker-owned')
            state = enable_verified_pack(root)
            self.assertEqual(state, {'state':'installed_verified', 'modules':['example']})
            self.assertEqual((root / 'runtime-status.json').read_text(), 'worker-owned')
            self.assertTrue(Path(os.environ['MSW_RUNTIME_PACK_ROOT']).is_dir())
            os.environ['MSW_RUNTIME_PACK_SHA256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'digest_mismatch'):
                enable_verified_pack(root)
            self.assertNotIn('MSW_RUNTIME_PACK_ROOT', os.environ)

    def test_observation_does_not_claim_queue_repair_or_disclose_extra_data(self):
        local = {'measurement': {'at': 10, 'cpu_percent': 80, 'memory_percent': 20,
                                 'fresh': True, 'secret': 'do-not-copy'}}
        hosting = {'at': 10, 'state': 'http', 'http_status': 503, 'outage_confirmed': True,
                   'sample_fresh': True, 'secret': 'do-not-copy'}
        with patch('msw_engine.doctor_sidecar.render_status', return_value=local), \
             patch('msw_engine.doctor_sidecar.failover_status', return_value=hosting):
            result = safe_observation(Path('/unused'))
        self.assertNotIn('secret', str(result))
        self.assertFalse(result['worker_queue_throttled_by_sidecar'])
        self.assertFalse(result['automatic_restart_after_instance_replacement_verified'])

    def test_shared_agent_memory_is_saved_and_retrievable(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            self.assertTrue(publish_evidence(directory, {'state': 'specific_render_incident'}))
            hits = knowledge_search({'query': 'specific_render_incident'}, directory)['items']
            self.assertEqual(hits[0]['id'], 'doctor:render:sidecar:latest')

    def test_sidecar_monitor_never_claims_it_defers_live_worker(self):
        with tempfile.TemporaryDirectory() as root:
            doctor = RenderDoctor(root, probe=Probe(), monitor_only=True)
            doctor.step(); current = doctor.step()
            self.assertFalse(current['main_queue_deferred'])
            self.assertFalse(doctor.defer_claim())
            self.assertEqual([item['kind'] for item in status(root)['recent_cases']], ['pressure_alarm'])

    def test_stage_is_pinned_and_detects_modified_or_corrupt_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'checkout'
            package = source / 'msw-autonomous-engine' / 'msw_engine'
            shutil.copytree(ROOT / 'msw_engine', package, ignore=shutil.ignore_patterns('__pycache__'))
            target_tool = source / 'msw-autonomous-engine' / 'tools'
            target_tool.mkdir(parents=True)
            shutil.copyfile(ROOT / 'tools' / 'stage_render_doctor.py', target_tool / 'stage_render_doctor.py')
            def git(*args):
                return subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()
            subprocess.run(['git', 'init', '-q', str(source)], check=True)
            git('add', '.')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'test')
            commit = git('rev-parse', 'HEAD')
            release = installer.stage(source, root / 'data', commit)
            self.assertTrue((release / 'msw_engine' / 'doctor_sidecar.py').is_file())
            self.assertEqual(installer.stage(source, root / 'data', commit), release)
            with self.assertRaisesRegex(ValueError, 'checkout_commit_mismatch'):
                installer.stage(source, root / 'data', 'a' * 40)
            path = package / 'doctor_sidecar.py'
            path.write_text(path.read_text() + '\n# modified\n')
            with self.assertRaisesRegex(ValueError, 'modified_source'):
                installer.stage(source, root / 'data', commit)
            git('checkout', '--', str(path.relative_to(source)))
            (release / 'msw_engine' / 'doctor_sidecar.py').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'existing_release_corrupt'):
                installer.stage(source, root / 'data', commit)

    def test_wrong_service_fails_before_any_write(self):
        with patch.dict(os.environ, {'RENDER_SERVICE_ID': 'other'}, clear=True), \
             patch('sys.argv', ['stage_render_doctor', '--expected-commit', 'a' * 40]):
            with self.assertRaisesRegex(SystemExit, 'wrong_render_service'):
                installer.main()


if __name__ == '__main__':
    unittest.main()
