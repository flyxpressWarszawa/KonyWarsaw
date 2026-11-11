"""Offline concurrency, HMAC and restart tests; no HTTP or paid model."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import hmac
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from msw_engine.client import Client, TransportError
from msw_engine.paid_attempt_guard import PaidAttemptGuard


class PaidConcurrentTests(unittest.TestCase):
    def test_inflight_guard_survives_worker_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            job = {"id": "invoice-offline-100", "payload_sha256": "a" * 64}
            payload = {"request_id": "offline-request-0001"}
            PaidAttemptGuard(Path(folder)).reserve(job, payload)
            restarted = PaidAttemptGuard(Path(folder))
            with self.assertRaisesRegex(TransportError, "paid_attempt_outcome_unverified"):
                restarted.reserve(job, payload)

    def test_same_client_concurrent_http_has_per_thread_tls_and_correct_signed_hmac(self):
        client = Client(
            "https://example.org", "https://example.org/transport.php",
            "unit-node", "X" * 40
        )
        barrier = threading.Barrier(4)
        lock = threading.Lock()
        openers, nonces, ids = [], [], []

        @contextmanager
        def fake_open(opener, request, *, timeout):
            self.assertEqual(timeout, 45)
            headers = {k.lower(): v for k, v in request.header_items()}
            job_id = json.loads(request.data)["job_id"]
            nonce = headers["x-mswue-nonce"]
            canonical = "\n".join([
                "POST", "status", "unit-node", headers["x-mswue-timestamp"],
                nonce, hashlib.sha256(request.data).hexdigest()
            ])
            expected = hmac.new(
                ("X" * 40).encode(), canonical.encode(), hashlib.sha256
            ).hexdigest()
            self.assertEqual(headers["x-mswue-signature"], expected)
            with lock:
                openers.append(opener)
                nonces.append(nonce)
                ids.append(job_id)
            barrier.wait(timeout=10)
            yield io.BytesIO(b'{"ok":true,"state":"mocked"}')

        def send(index):
            return client.send("status", {"job_id": "offline-" + str(index)})

        with patch("msw_engine.client.protected_open", side_effect=fake_open):
            with ThreadPoolExecutor(max_workers=4) as pool:
                responses = list(pool.map(send, range(4)))
        self.assertEqual([r["state"] for r in responses], ["mocked"] * 4)
        self.assertEqual(len({id(o) for o in openers}), 4)
        self.assertEqual(len(set(nonces)), 4)
        self.assertEqual(set(ids), {"offline-0", "offline-1", "offline-2", "offline-3"})


if __name__ == "__main__":
    unittest.main()
