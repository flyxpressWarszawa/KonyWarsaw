"""Integration tests use a private pack supplied by its owner, never in Git."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

from msw_engine.runtime_pack import unpack
from msw_engine.tasks import dispatch, TaskError


@unittest.skipUnless(os.environ.get("MSW_TEST_PACK"), "owner-supplied private runtime pack required")
class PrivateAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        raw = Path(os.environ["MSW_TEST_PACK"]).read_bytes()
        root, _ = unpack(raw, hashlib.sha256(raw).hexdigest(), self.directory)
        self.previous_root = os.environ.get("MSW_RUNTIME_PACK_ROOT")
        os.environ["MSW_RUNTIME_PACK_ROOT"] = str(root)

    def tearDown(self):
        if self.previous_root is None:
            os.environ.pop("MSW_RUNTIME_PACK_ROOT", None)
        else:
            os.environ["MSW_RUNTIME_PACK_ROOT"] = self.previous_root
        self.temp.cleanup()

    def run_job(self, kind, payload):
        return dispatch(kind, payload, self.directory)

    def test_supreme_persists_one_idempotent_inventory_observation(self):
        payload = {"origin": "https://mobilny-serwis-warszawa.pl", "provenance": "authenticated_wordpress_inventory",
                   "inventory": {"installed": 10, "active": 8, "inactive": 2}}
        first = self.run_job("supreme.observe", payload)
        self.assertEqual(first["record_id"], self.run_job("supreme.observe", payload)["record_id"])
        with sqlite3.connect(self.directory / "supreme/msw-supreme-state.sqlite3") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM memory").fetchone()[0], 1)
        self.assertFalse(first["v5_native_ack"])
        with self.assertRaises(TaskError):
            self.run_job("supreme.observe", {**payload, "inventory": {"installed": 10, "active": True, "inactive": 9}})

    def test_combo_original_receiver_deduplicates_and_rejects_changed_input(self):
        payload = {"schema": "msw.combo.analytics.v1", "request_id": "combo-" + "1" * 40,
                   "origin": "https://mobilny-serwis-warszawa.pl", "generated_utc": "2026-11-05T00:00:00Z",
                   "published_total": 10, "published_last_30_days": 2, "top_search_pages": []}
        first = self.run_job("combo.analytics", payload)
        self.assertTrue(first["accepted"])
        self.assertTrue(self.run_job("combo.analytics", payload)["deduplicated"])
        self.assertEqual(first["insights"], [])
        with self.assertRaisesRegex(TaskError, "idempotency_conflict"):
            self.run_job("combo.analytics", {**payload, "published_total": 11})
        with self.assertRaises(TaskError):
            self.run_job("combo.analytics", {**payload, "origin": "https://elsewhere.example"})

    def envelope(self, ident=None, text="Public test source — \u2028"):
        body = {"id": ident or str(uuid.uuid4()), "schema": "msw.engine.dataset.v1", "day": "2026-11-05",
                "kind": "content", "source": "wordpress_publication", "origin": "https://mobilny-serwis-warszawa.pl",
                "record_id": "450", "data": {"text": text, "float": 1.0}}
        unsigned = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        return {"raw": unsigned[:-1] + ',"digest":"' + hashlib.sha256(unsigned.encode()).hexdigest() + '"}'}

    def test_v3_preserves_exact_source_bytes_and_checks_receipt(self):
        payload = self.envelope()
        first = self.run_job("engine.v3.ingest", payload)
        self.assertTrue(first["stored"])
        self.assertTrue(first["durable_local_readback_verified"])
        self.assertEqual(first, self.run_job("engine.v3.ingest", payload))
        with sqlite3.connect(self.directory / "engine-v3/datasets.sqlite3") as db:
            self.assertEqual(db.execute("SELECT body FROM msw_datasets").fetchone()[0], payload["raw"])
        with self.assertRaises(Exception) as raised:
            self.run_job("engine.v3.ingest", self.envelope(first["id"], "changed source"))
        self.assertEqual(raised.exception.status_code, 409)

    def test_v3_rejects_digest_tampering_and_extra_envelope_fields(self):
        payload = self.envelope()
        with self.assertRaises(Exception) as raised:
            self.run_job("engine.v3.ingest", {"raw": payload["raw"].replace("Public test", "Tampered")})
        self.assertEqual(raised.exception.status_code, 422)
        with self.assertRaises(TaskError):
            self.run_job("engine.v3.ingest", {**payload, "command": "unapproved"})

    def test_scheduler_finds_optimum_without_dispatching(self):
        problem = {"currency": "PLN", "travel_buffer_minutes": 0, "candidates": [
            {"id": "a", "job_id": "a", "technician_id": "t1", "start_minute": 0, "end_minute": 60, "margin_cents": 10000},
            {"id": "b", "job_id": "b", "technician_id": "t1", "start_minute": 0, "end_minute": 60, "margin_cents": 15000}]}
        result = self.run_job("quantum.schedule", {"problem": problem, "max_seconds": 1, "max_nodes": 1000})
        self.assertEqual(result["selected_ids"], ["b"])
        self.assertEqual(result["objective_cents"], result["upper_bound_cents"])
        self.assertTrue(result["optimal"])
        self.assertFalse(result["dispatch_performed"])

    def test_simulator_never_submits_hardware_jobs(self):
        for circuit, qubits in (("hadamard", 1), ("bell", 2), ("ghz3", 3)):
            result = self.run_job("quantum.simulate", {"circuit": circuit})
            self.assertEqual(result["remote_hardware_jobs"], 0)
            self.assertEqual(result["simulated_qubits"], qubits)
            self.assertAlmostEqual(sum(result["probabilities"].values()), 1)
        with self.assertRaises(TaskError):
            self.run_job("quantum.simulate", {"circuit": "bell", "provider_url": "https://example.test"})

    def test_fotonika_classifier_can_be_reopened_without_changing_predictions(self):
        payload = {"action": "train", "x": [[0, 0], [0, 1], [1, 0], [1, 1], [.1, .1], [.1, .9], [.9, .1], [.9, .9]],
                   "y": [0, 1, 1, 0, 0, 1, 1, 0], "predict_x": [[0, 0], [1, 1]],
                   "num_features": 2, "max_iterations": 3, "seed": 42}
        result = self.run_job("fotonika.qnn", payload)
        self.assertTrue(result["durable_local_readback_verified"])
        self.assertTrue((self.directory / "fotonika/models" / (result["model_id"] + ".json")).is_file())
        self.assertFalse(result["quantum_hardware"])
        self.assertFalse(result["generalization_validated"])
        self.assertFalse(result["llm_weights_modified"])
        with self.assertRaises(TaskError):
            self.run_job("fotonika.qnn", {**payload, "optimizer": "COBYLA"})


if __name__ == "__main__":
    unittest.main()
