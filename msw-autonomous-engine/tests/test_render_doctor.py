import tempfile
import unittest
from pathlib import Path

from msw_engine.render_doctor import CgroupProbe, RenderDoctor, search_cases, status
from msw_engine.runner import Worker


class Clock:
    def __init__(self): self.value = 1000
    def __call__(self): return self.value


class Probe:
    def __init__(self, samples): self.samples = iter(samples)
    def sample(self): return next(self.samples)


class RenderDoctorTests(unittest.TestCase):
    def test_cgroup_deltas_use_allocated_cpu_not_host_cpu(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / 'cpu.max').write_text('200000 100000')
            (root / 'cpu.stat').write_text('usage_usec 1000000\n')
            (root / 'memory.current').write_text('40')
            (root / 'memory.max').write_text('100')
            probe = CgroupProbe(root, clock)
            self.assertIsNone(probe.sample()['cpu_percent'])
            clock.value += 10
            (root / 'cpu.stat').write_text('usage_usec 15000000\n')
            result = probe.sample()
            self.assertEqual(result['cpu_percent'], 70)
            self.assertEqual(result['memory_percent'], 40)
            (root / 'cpu.max').write_text('max 100000')
            clock.value += 10
            self.assertIsNone(probe.sample()['cpu_percent'])

    def test_alarm_then_bounded_queue_deferral_and_verified_memory(self):
        clock = Clock()
        samples = [{'cpu_percent': 71, 'memory_percent': 20},
                   {'cpu_percent': 91, 'memory_percent': 20},
                   {'cpu_percent': 95, 'memory_percent': 20},
                   {'cpu_percent': 64, 'memory_percent': 30},
                   {'cpu_percent': 60, 'memory_percent': 31}]
        with tempfile.TemporaryDirectory() as root:
            doctor = RenderDoctor(root, probe=Probe(samples), clock=clock)
            doctor.step()
            self.assertTrue(doctor.high_pressure())
            self.assertFalse(doctor.defer_claim())
            doctor.step()
            self.assertFalse(doctor.defer_claim())
            doctor.step()
            self.assertTrue(doctor.defer_claim())
            doctor.step()
            self.assertTrue(doctor.defer_claim())
            result = doctor.step()
            self.assertFalse(result['main_queue_deferred'])
            saved = status(root)
            self.assertEqual(saved['case_count'], 4)
            self.assertEqual(saved['recent_cases'][0]['kind'], 'pressure_recovered')
            self.assertTrue(all(case['storage_readback_verified'] for case in saved['recent_cases']))
            self.assertTrue(saved['memory_is_evidence_not_instruction'])
            self.assertFalse(saved['hard_cpu_limit_enforced'])
            self.assertFalse(saved['render_api_connected'])
            self.assertEqual(search_cases(root, 'queue deferred')[0]['kind'], 'queue_deferred')
            self.assertEqual(search_cases(root, 'term_not_in_memory'), [])

    def test_unknown_or_old_sample_does_not_hold_queue(self):
        clock = Clock()
        samples = [{'cpu_percent': 95, 'memory_percent': 10}, {'cpu_percent': 95, 'memory_percent': 10},
                   {'cpu_percent': None, 'memory_percent': None}]
        with tempfile.TemporaryDirectory() as root:
            doctor = RenderDoctor(root, probe=Probe(samples), clock=clock)
            doctor.step(); doctor.step()
            self.assertTrue(doctor.defer_claim())
            clock.value += 91
            self.assertFalse(doctor.defer_claim())
            doctor.step()
            self.assertFalse(doctor.defer_claim())

    def test_deferred_queue_still_replays_receipts_and_does_not_claim(self):
        class Client:
            def send(self, action, payload): raise AssertionError('must not claim new work')
        class Doctor:
            def defer_claim(self): return True
        with tempfile.TemporaryDirectory() as root:
            worker = Worker(Client(), Path(root))
            calls = []
            worker.replay_receipts = lambda: calls.append('replayed')
            worker.doctor = Doctor()
            self.assertEqual(worker.run_once()['state'], 'doctor_deferred')
            self.assertEqual(calls, ['replayed'])


if __name__ == '__main__': unittest.main()
