import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from msw_engine.client import TransportError
from msw_engine.runner import Worker


class PHPEmptyArgumentTests(unittest.TestCase):
    def job(self, raw, kind="runtime.catalog"):
        return {"id": "php-empty-arguments-test", "kind": kind, "payload": raw,
                "payload_sha256": hashlib.sha256(raw.encode()).hexdigest()}

    def test_authenticated_empty_php_arguments_run_no_argument_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = Worker(None, Path(folder), memory_mb=0)
            receipt = worker.execute(self.job("[]"), "local-test-lease")
            self.assertTrue(json.loads(receipt["result_json"])["ok"])

    def test_nonempty_arrays_and_other_job_kinds_stay_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = Worker(None, Path(folder), memory_mb=0)
            for job in (self.job('["unexpected"]'), self.job("[]", "knowledge.ingest")):
                with self.assertRaises(TransportError):
                    worker.execute(job, "local-test-lease")

    def test_hash_verification_precedes_empty_argument_normalization(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = Worker(None, Path(folder), memory_mb=0)
            job = {**self.job("[]"), "payload_sha256": "0" * 64}
            with self.assertRaises(TransportError):
                worker.execute(job, "local-test-lease")


if __name__ == "__main__":
    unittest.main()
