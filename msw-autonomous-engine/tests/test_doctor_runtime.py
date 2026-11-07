import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from msw_engine.runner import Worker


class Client:
    wordpress_url = 'https://mobilny-serwis-warszawa.pl'


class RuntimeTests(unittest.TestCase):
    def run_worker(self, enabled, once=False):
        env = {'MSW_DOCTOR_RUNTIME': '1' if enabled else '0', 'MSW_RENDER_DOCTOR': '0',
               'MSW_FAILOVER_DOCTOR': '0', 'MSW_CATALOG_PUBLISHER': '0',
               'MSW_AUTONOMOUS_AGENT': '0', 'MSW_RUNTIME_MODE': 'render-free'}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, env, clear=True), \
             patch('msw_engine.runner.signal.signal'), \
             patch('msw_engine.doctor_compute_lane.ComputeLane') as compute, \
             patch('msw_engine.doctor_receipts.ReceiptDoctor') as receipts:
            worker = Worker(Client(), Path(tmp))
            def one():
                worker.stopping = True
                return {'state': 'idle'}
            worker.run_once = one
            result = worker.loop(once=once, poke_cron_seconds=0)
            self.assertEqual(result, 0)
            self.assertEqual(compute.call_count, int(enabled and not once))
            self.assertEqual(receipts.call_count, int(enabled and not once))
            if enabled and not once:
                compute.return_value.start.assert_called_once()
                receipts.return_value.start.assert_called_once()

    def test_enabled_runtime_starts_both_supervisors(self): self.run_worker(True)
    def test_disabled_runtime_does_not_start_supervisors(self): self.run_worker(False)
    def test_one_shot_does_not_start_supervisors(self): self.run_worker(True, once=True)


if __name__ == '__main__': unittest.main()
