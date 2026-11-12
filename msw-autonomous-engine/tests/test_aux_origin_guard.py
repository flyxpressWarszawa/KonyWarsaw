import hashlib
import json
import io
import math
import unittest
from contextlib import contextmanager
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError

from msw_engine.client import TransportError
from msw_engine.render_auxiliary import OriginPressureGate, next_poll_delay, read_origin_health, run_cycle, step


NOW = 10000


def sample(at, cpu=20, ram=20):
    return {"schema": "msw.render.account-health.v1", "state": "fresh", "measured_at": at,
            "cpu_percent": cpu, "physical_memory_percent": ram}


class DummyWorker:
    def __init__(self):
        self.replayed = 0

    def replay_receipts(self, *, limit=8):
        if limit != 1:
            raise AssertionError("auxiliary receipt budget")
        self.replayed += 1


class NoClaimClient:
    def __init__(self):
        self.calls = []

    def send(self, action, data):
        self.calls.append((action, data))
        raise AssertionError("unexpected queue request")


class QueueClient:
    def __init__(self):
        self.claims = 0

    def send(self, action, data):
        if action != "claim":
            raise AssertionError(action)
        self.claims += 1
        jobs = [{"job": {"kind": "seo.analyze", "id": f"{self.claims}-{index}",
                          "payload": "{}", "payload_sha256": hashlib.sha256(b"{}").hexdigest()},
                 "lease_token": "test-lease", "lease_seconds": 900}
                for index in range(data.get("batch_limit", 1))]
        return {"state": "claimed_batch", "batch_protocol": "msw.seo.batch.v1", "jobs": jobs}


class CompletionWorker(DummyWorker):
    def execute(self, job, lease):
        return {"id": job["id"], "result_sha256": "test-digest"}

    def acknowledge(self, receipt):
        return {"state": "completed", "job_id": receipt["id"], "server_ack_verified": True}


class StopStub:
    def is_set(self):
        return False

    def wait(self, seconds):
        return False


class SequenceGate:
    def __init__(self, *states):
        self.states = list(states)

    def check(self, client):
        return {"state": self.states.pop(0)}


class OriginGuardTests(unittest.TestCase):
    def setUp(self):
        self.current = sample(NOW - 1)
        self.gate = OriginPressureGate(reader=lambda _: self.current, clock=lambda: NOW)
        self.client = NoClaimClient()
        self.worker = DummyWorker()

    def test_two_distinct_recent_low_samples_open_gate(self):
        self.assertEqual(self.gate.check(self.client)["state"], "origin_warmup")
        self.assertEqual(self.gate.check(self.client)["state"], "origin_warmup")
        self.current = sample(NOW)
        self.assertEqual(self.gate.check(self.client)["state"], "ready")
        self.assertEqual(self.gate.check(self.client)["state"], "ready")

    def test_memory_pressure_never_claims_and_replays_old_receipts(self):
        self.current = sample(NOW, cpu=70, ram=70)
        result = step(self.client, self.worker, gate=self.gate)
        self.assertEqual(result["state"], "origin_pressure")
        self.assertEqual(self.worker.replayed, 1)
        self.assertEqual(self.client.calls, [])

    def test_ram_and_cpu_pressure_both_close_an_open_gate(self):
        self.gate.check(self.client)
        self.current = sample(NOW)
        self.assertEqual(self.gate.check(self.client)["state"], "ready")
        for cpu, ram, state in [(70, 10, "pressure_offload"), (10, 70, "origin_pressure")]:
            self.current = sample(NOW, cpu=cpu, ram=ram)
            self.assertEqual(self.gate.check(self.client)["state"], state)
            self.assertFalse(self.gate.open)

    def test_fresh_cpu_pressure_claims_only_one_job(self):
        self.current = sample(NOW, cpu=100, ram=57)
        client, worker = QueueClient(), CompletionWorker()
        with patch("msw_engine.render_auxiliary.emit"):
            self.assertEqual(run_cycle(client, worker, self.gate, StopStub()), "completed")
        self.assertEqual(client.claims, 1)
        self.assertEqual(worker.replayed, 1)

    def test_stale_future_nan_bool_and_bad_schema_fail_closed(self):
        invalid = [sample(NOW - 91), sample(NOW + 1), sample(NOW, cpu=math.nan),
                   sample(NOW, cpu=True), {**sample(NOW), "schema": "other"}, {}]
        for value in invalid:
            with self.subTest(value=value):
                self.current = value
                self.assertEqual(step(self.client, self.worker, gate=self.gate)["state"], "origin_unknown")
        self.assertEqual(self.client.calls, [])

    def test_telemetry_gap_and_backwards_timestamp_require_new_warmup(self):
        self.gate.check(self.client)
        self.current = sample(NOW)
        self.assertEqual(self.gate.check(self.client)["state"], "ready")
        self.current = {}
        self.assertEqual(self.gate.check(self.client)["state"], "origin_unknown")
        self.current = sample(NOW - 1)
        self.assertEqual(self.gate.check(self.client)["state"], "origin_warmup")
        self.current = sample(NOW - 2)
        self.assertEqual(self.gate.check(self.client)["state"], "origin_unknown")

    def test_transport_failure_closes_gate_until_two_new_low_samples(self):
        self.gate.check(self.client)
        self.current = sample(NOW)
        self.assertEqual(self.gate.check(self.client)["state"], "ready")

        def failed_reader(_):
            raise TransportError("offline", 503, 300)

        self.gate.reader = failed_reader
        with self.assertRaises(TransportError):
            self.gate.check(self.client)
        self.assertFalse(self.gate.open)
        self.gate.reader = lambda _: self.current
        self.assertEqual(self.gate.check(self.client)["state"], "origin_warmup")
        self.assertEqual(self.gate.check(self.client)["state"], "origin_warmup")

    def test_long_unobserved_interval_requires_two_new_low_samples(self):
        clock = [NOW]
        current = [sample(NOW - 1)]
        gate = OriginPressureGate(reader=lambda _: current[0], clock=lambda: clock[0])
        self.assertEqual(gate.check(self.client)["state"], "origin_warmup")
        current[0] = sample(NOW)
        self.assertEqual(gate.check(self.client)["state"], "ready")
        clock[0] = NOW + 91
        current[0] = sample(clock[0])
        self.assertEqual(gate.check(self.client)["state"], "origin_warmup")
        self.assertFalse(gate.open)
        clock[0] += 1
        current[0] = sample(clock[0])
        self.assertEqual(gate.check(self.client)["state"], "ready")

    def test_recheck_preserves_freshness_with_minute_sample_updates(self):
        # A fresh but 50-second-old first sample expires before a 60-second
        # recheck, even though the next producer sample is itself fresh.
        clock = [NOW]
        current = [sample(NOW - 50)]
        gate = OriginPressureGate(reader=lambda _: current[0], clock=lambda: clock[0])
        first = gate.check(self.client)
        self.assertEqual(first["low_samples"], 1)
        self.assertEqual(first["sample_age_seconds"], 50)
        self.assertEqual(next_poll_delay(first["state"], gate.low_samples), 25)
        clock[0] += 25
        current[0] = sample(NOW + 10)
        self.assertEqual(gate.check(self.client)["state"], "ready")

        # Under the original 60-second interval the same valid sequence
        # resets the previous sample and the worker still cannot claim.
        clock[0], current[0] = NOW, sample(NOW - 50)
        old_gate = OriginPressureGate(reader=lambda _: current[0], clock=lambda: clock[0])
        old_gate.check(self.client)
        clock[0] += 60
        current[0] = sample(NOW + 10)
        delayed = old_gate.check(self.client)
        self.assertEqual(delayed["state"], "origin_warmup")
        self.assertTrue(delayed["sample_window_reset"])
        self.assertEqual(delayed["previous_sample_age_seconds"], 110)

    def test_faster_recheck_never_counts_the_same_sample_twice(self):
        clock = [NOW]
        current = [sample(NOW - 20)]
        gate = OriginPressureGate(reader=lambda _: current[0], clock=lambda: clock[0])
        self.assertEqual(gate.check(self.client)["low_samples"], 1)
        clock[0] += 25
        self.assertEqual(gate.check(self.client)["low_samples"], 1)
        self.assertFalse(gate.open)
        clock[0] += 25
        current[0] = sample(clock[0], cpu=70)
        self.assertEqual(gate.check(self.client)["state"], "pressure_offload")
        self.assertFalse(gate.open)

    def test_faster_recheck_only_follows_one_low_sample(self):
        self.assertEqual(next_poll_delay("origin_warmup", 1), 25)
        self.assertEqual(next_poll_delay("origin_warmup", 0), 60)
        for state in ("origin_pressure", "pressure_offload", "origin_unknown", "idle", "completed"):
            self.assertEqual(next_poll_delay(state, 1), 300)

    def test_503_retry_after_is_not_shortened(self):
        headers = Message()
        headers["Retry-After"] = "1800"
        error = HTTPError("https://example.test/health", 503, "busy", headers, None)

        @contextmanager
        def failed_open(*args, **kwargs):
            raise error
            yield  # pragma: no cover

        class Stub:
            wordpress_url = "https://example.test"
            node = "node"
            secret = "a" * 64
            opener = object()

        with patch("msw_engine.render_auxiliary.protected_open", failed_open):
            with self.assertRaises(TransportError) as caught:
                read_origin_health(Stub())
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(caught.exception.retry_after, 1800)

    def test_health_request_is_signed_and_uses_the_expected_fixed_endpoint(self):
        captured = []
        expected = sample(NOW)

        @contextmanager
        def fake_open(opener, request, timeout):
            captured.append((request, timeout))
            yield io.BytesIO(json.dumps(expected).encode())

        class Stub:
            wordpress_url = "https://example.test"
            node = "node"
            secret = "a" * 64
            opener = object()

        with patch("msw_engine.render_auxiliary.protected_open", fake_open):
            self.assertEqual(read_origin_health(Stub()), expected)
        req, timeout = captured[0]
        self.assertEqual(req.full_url, "https://example.test/wp-content/plugins/msw-unified-engine/msw-render-account-health.php")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(json.loads(req.data), {"schema": "msw.render.account-health.v1"})
        self.assertTrue(any("signature" in name.lower() and value for name, value in req.header_items()))
        self.assertEqual(timeout, 12)

    def test_step_cannot_be_called_without_an_explicit_gate(self):
        with self.assertRaises(TypeError):
            step(self.client, self.worker)
        self.assertEqual(self.client.calls, [])

    def test_next_cycle_does_not_admit_work_if_pressure_appears(self):
        client, worker = QueueClient(), CompletionWorker()
        gate = SequenceGate("ready", "origin_pressure")
        with patch("msw_engine.render_auxiliary.emit"):
            self.assertEqual(run_cycle(client, worker, gate, StopStub()), "completed")
            state = run_cycle(client, worker, gate, StopStub())
        self.assertEqual(state, "origin_pressure")
        self.assertEqual(client.claims, 1)
        self.assertEqual(worker.replayed, 2)

    def test_healthy_cycle_uses_one_admission_for_at_most_four_jobs(self):
        client, worker = QueueClient(), CompletionWorker()
        with patch("msw_engine.render_auxiliary.emit") as emitted:
            state = run_cycle(client, worker, SequenceGate("ready"), StopStub())
        self.assertEqual(state, "completed")
        self.assertEqual(client.claims, 1)
        self.assertEqual(worker.replayed, 1)
        self.assertEqual(sum(call.args[0] == "completed" for call in emitted.call_args_list), 4)


if __name__ == "__main__":
    unittest.main()


