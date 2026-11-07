from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from msw_engine.render_deploy_recovery import DeployDoctor


class Clock:
    def __init__(self): self.value = 100000
    def __call__(self): return self.value


class DeployTests(unittest.TestCase):
    def fixture(self):
        clock = Clock(); calls = []
        old = datetime.fromtimestamp(clock.value-11000, timezone.utc).isoformat()
        deploy = {'id': 'dep-abc123', 'status': 'build_in_progress', 'startedAt': old, 'updatedAt': old}
        config = {'deploy': deploy, 'uncertain': False, 'override': None, 'extra': [], 'live': True}
        def send(method, suffix, key):
            calls.append((method, suffix))
            if suffix == '/deploys?limit=20':
                body = [{'deploy': config['deploy']}] + ([{'deploy': {'id': 'dep-live', 'status': 'live'}}] if config['live'] else []) + config['extra']
                return {'ok': True, 'body': body}
            if method == 'POST':
                if not config['uncertain']: config['deploy']['status'] = 'canceled'
                return {'ok': not config['uncertain'], 'body': config['deploy']}
            return {'ok': True, 'body': config['override'] or config['deploy']}
        env = {'MSW_RENDER_DOCTOR_API_KEY': 'K'*40, 'MSW_RENDER_DOCTOR_CONTROLLER': 'render'}
        return clock, calls, config, send, env

    def test_unconfigured_controller_has_no_api_calls(self):
        clock, calls, _, send, _ = self.fixture()
        with tempfile.TemporaryDirectory() as root:
            doctor = DeployDoctor(root, send=send, env={}, clock=clock)
            self.assertEqual(doctor.step()['state'], 'controller_not_configured'); self.assertEqual(calls, [])

    def test_confirmed_stall_cancelled_once_and_read_back(self):
        clock, calls, _, send, env = self.fixture()
        with tempfile.TemporaryDirectory() as root:
            doctor = DeployDoctor(root, send=send, env=env, clock=clock)
            doctor.step(); clock.value += 300; doctor.step(); self.assertFalse(any(x[0]=='POST' for x in calls))
            clock.value += 300; result = doctor.step()
            self.assertEqual(result['action_state'], 'cancel_verified')
            self.assertEqual(sum(x[0]=='POST' for x in calls), 1)
            self.assertNotIn('K'*40, json.dumps(doctor.load()))
            clock.value += 300; doctor.step(); self.assertEqual(sum(x[0]=='POST' for x in calls), 1)

    def test_uncertain_cancel_is_reconciled_without_repeating(self):
        clock, calls, config, send, env = self.fixture(); config['uncertain'] = True
        with tempfile.TemporaryDirectory() as root:
            doctor = DeployDoctor(root, send=send, env=env, clock=clock)
            doctor.step(); clock.value += 300; doctor.step(); clock.value += 300
            self.assertEqual(doctor.step()['action_state'], 'cancel_outcome_unverified')
            clock.value += 300; self.assertEqual(doctor.step()['action_state'], 'cancel_outcome_unverified')
            self.assertEqual(sum(x[0]=='POST' for x in calls), 1)
            config['deploy']['status'] = 'canceled'; clock.value += 300
            self.assertEqual(doctor.step()['action_state'], 'cancel_verified')
            self.assertEqual(sum(x[0]=='POST' for x in calls), 1)

    def test_multiple_active_or_missing_live_service_prevents_cancel(self):
        clock, calls, config, send, env = self.fixture()
        with tempfile.TemporaryDirectory() as root:
            doctor = DeployDoctor(root, send=send, env=env, clock=clock)
            config['extra'] = [{'deploy': {'id': 'dep-other', 'status': 'queued'}}]
            self.assertEqual(doctor.step()['state'], 'no_single_stalled_build')
            config['extra'] = []; config['live'] = False; clock.value += 300
            self.assertEqual(doctor.step()['state'], 'no_single_stalled_build')
            self.assertFalse(any(x[0]=='POST' for x in calls))

    def test_fresh_recheck_prevents_stale_action(self):
        clock, calls, config, send, env = self.fixture()
        with tempfile.TemporaryDirectory() as root:
            doctor = DeployDoctor(root, send=send, env=env, clock=clock)
            doctor.step(); clock.value += 300; doctor.step(); clock.value += 300
            config['override'] = {**config['deploy'], 'status': 'live'}
            doctor.step(); self.assertFalse(any(x[0]=='POST' for x in calls))


if __name__ == '__main__': unittest.main()
