import json
import os
from pathlib import Path
import signal
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from msw_engine.lifecycle import finish_threads, drain_requested
from msw_engine.runner import Worker, WorkerDraining
from msw_engine.priority_lane import PriorityLane
from msw_engine.fleet_scheduler import consume_data


class LifecycleTests(unittest.TestCase):
    def test_marker_refuses_claim_without_cancelling_admitted_work(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            client = Mock()
            worker = Worker(client, root)
            (root / 'drain.request').touch()
            with self.assertRaises(WorkerDraining):
                worker.run_once()
            client.send.assert_not_called()
            worker.check_draining()  # Soft drain lets an admitted job persist and ACK.

    def test_waits_for_inflight_thread_to_persist(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            entered, finish = threading.Event(), threading.Event()
            def work():
                entered.set()
                finish.wait()
                (root / 'receipt').write_text('committed')
            thread = threading.Thread(target=work, name='inflight')
            thread.start()
            entered.wait(2)
            timer = threading.Timer(.2, finish.set)
            timer.start()
            try:
                self.assertEqual(finish_threads([thread], root, lambda: False), [])
                self.assertEqual((root / 'receipt').read_text(), 'committed')
            finally:
                finish.set(); thread.join(); timer.cancel()

    def test_hard_stop_timeout_never_reports_drained(self):
        release = threading.Event()
        thread = threading.Thread(target=release.wait, name='still-writing')
        thread.start()
        try:
            self.assertEqual(finish_threads([thread], Path('.'), lambda: True, grace_seconds=.01), ['still-writing'])
        finally:
            release.set(); thread.join()

    def test_loop_waits_for_all_tracked_threads_and_holds_until_signal(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            worker = Worker(Mock(), root)
            events = []
            def step(**kwargs):
                (root / 'drain.request').touch()
                def tail():
                    time.sleep(.15)
                    events.append('saved')
                thread = threading.Thread(target=tail, name='test-tail')
                thread.start()
                worker.track(thread)
                return 0
            def stop_after_readback():
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    path = root / 'drain-status.json'
                    if path.exists():
                        data = json.loads(path.read_text())
                        if data['state'] == 'drained':
                            events.append('drained')
                            worker.stopping = True
                            return
                    time.sleep(.02)
                worker.stopping = True
            monitor = threading.Thread(target=stop_after_readback)
            monitor.start()
            with patch.object(worker, '_loop', side_effect=step):
                self.assertEqual(worker.loop(), 0)
            monitor.join()
            self.assertEqual(events, ['saved', 'drained'])

    def test_priority_keeps_hard_cancel_separate_from_admission(self):
        with tempfile.TemporaryDirectory() as raw:
            client = Mock()
            stop = [False]
            def claim(*args):
                stop[0] = True
                return {'state': 'claimed', 'job': {'kind': 'llm.generate'}, 'lease_token': 'lease'}
            client.send.side_effect = claim
            execution = Mock()
            execution.acknowledge.return_value = {'state': 'completed'}
            lane = PriorityLane(client, Path(raw), worker_factory=lambda *a, **k: execution)
            lane.step(lambda: stop[0], lambda: False)
            self.assertFalse(execution.cancel_check())
            execution.execute.assert_called_once()

    def test_data_lane_refuses_soft_drained_claim(self):
        client = Mock()
        result = consume_data(client, Path('.'), lambda: False, lambda: True)
        self.assertEqual(result['state'], 'draining')
        client.send.assert_not_called()
