import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from msw_engine import render_auxiliary as auxiliary
from msw_engine.client import TransportError
from msw_engine.runner import Worker, WorkerDraining


def entry(index):
    raw = json.dumps({"title": f"Public fixture {index}", "content": "spawanie aluminium Warszawa",
                      "html": "<h2>Spawanie</h2><p>Aluminium</p>", "url": "https://example.test/a"})
    return {"job": {"id": f"00000000-0000-0000-0000-{index:012d}", "kind": "seo.analyze", "payload": raw,
                    "payload_sha256": hashlib.sha256(raw.encode()).hexdigest()},
            "lease_token": "fixture-lease-" + str(index), "lease_seconds": 900}


def batch(count=4):
    return {"state": "claimed_batch", "batch_protocol": "msw.seo.batch.v1", "jobs": [entry(i) for i in range(count)]}


class Stop:
    stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, _):
        return self.stopped


class Gate:
    def __init__(self, state="ready"):
        self.state, self.calls = state, 0

    def check(self, _):
        self.calls += 1
        return {"state": self.state}


class Client:
    wordpress_url = "https://example.test"

    def __init__(self, response=None):
        self.response = {"state": "claimed", **entry(0)} if response is None else response
        self.calls, self.results = [], {}
        self.after_status = None
        self.fail_status_once = None

    def send(self, action, payload):
        self.calls.append((action, payload))
        if action == "claim":
            response, self.response = self.response, {"state": "idle"}
            return response
        ident = payload["job_id"]
        if action == "complete":
            self.results[ident] = dict(payload)
            return {"stored": True, "result_sha256": payload["result_sha256"]}
        if action == "status":
            if ident == self.fail_status_once:
                self.fail_status_once = None
                raise TransportError("fixture_timeout", 503)
            if self.after_status:
                self.after_status(ident)
            return {"state": "completed", "result_sha256": self.results[ident]["result_sha256"]}
        raise AssertionError(action)


class AuxiliaryBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        env = patch.dict(os.environ, {"MSW_RUNTIME_MODE": "render-free", "MSW_AUTONOMOUS_AGENT": "0",
                                      "MSW_DATA_DIR": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)

    def test_one_real_seo_execution_uses_one_claim_and_verified_receipt(self):
        client, gate = Client(), Gate()
        worker = Worker(client, self.directory, max_seconds=30, memory_mb=256)
        with patch.object(auxiliary, "emit"):
            self.assertEqual(auxiliary.run_cycle(client, worker, gate, Stop()), "completed")
        self.assertEqual(gate.calls, 1)
        claims = [p for a, p in client.calls if a == "claim"]
        self.assertEqual(claims, [{"capabilities": ["seo.analyze", "research.rank"]}])
        self.assertEqual([a for a, _ in client.calls].count("complete"), 1)
        self.assertEqual([a for a, _ in client.calls].count("status"), 1)
        for receipt in client.results.values():
            result = json.loads(receipt["result_json"])
            self.assertTrue(result["ok"])
            self.assertEqual(result["data"]["word_count"], 3)
        self.assertEqual(worker.journal.pending(), [])

    def test_research_rank_executes_on_production_cycle_with_verified_receipt(self):
        item = entry(7)
        raw = json.dumps({"ideas": [{"id": "service", "demand": 80, "competition": 20,
                                      "margin": 70, "delivery_effort": 30,
                                      "sources": ["https://example.test/evidence"]}]})
        item["job"].update(kind="research.rank", payload=raw,
                           payload_sha256=hashlib.sha256(raw.encode()).hexdigest())
        client = Client({"state": "claimed", **item})
        with patch.object(auxiliary, "emit"):
            self.assertEqual(auxiliary.run_cycle(client, Worker(client, self.directory), Gate(), Stop()), "completed")
        result = json.loads(next(iter(client.results.values()))["result_json"])
        self.assertEqual(result["data"]["ideas"][0]["id"], "service")
        self.assertEqual(result["data"]["ideas"][0]["score"], 76.0)

    def test_every_entry_is_validated_before_any_execution(self):
        corruptions = []
        value = batch(); value["jobs"][-1]["job"]["kind"] = "module.execute"; corruptions.append(value)
        value = batch(); value["jobs"][-1]["job"]["payload_sha256"] = "bad"; corruptions.append(value)
        value = batch(); value["jobs"][-1] = copy.deepcopy(value["jobs"][0]); corruptions.append(value)
        value = batch(); value["jobs"][-1]["lease_seconds"] = True; corruptions.append(value)
        value = batch(); value["jobs"][-1]["lease_seconds"] = 30; corruptions.append(value)
        value = batch(5); corruptions.append(value)
        value = batch(0); corruptions.append(value)
        value = batch(); value["batch_protocol"] = "unknown"; corruptions.append(value)
        for value in corruptions:
            with self.subTest(value=value.get("batch_protocol")):
                client = Client(value)
                worker = Worker(client, self.directory)
                with self.assertRaises(auxiliary.ProtocolError), patch.object(auxiliary, "emit"):
                    auxiliary.run_cycle(client, worker, Gate(), Stop())
                self.assertEqual([a for a, _ in client.calls], ["claim"])
                self.assertEqual(worker.journal.pending(), [])

    def test_mixed_batch_is_rejected_before_any_execution_under_one_job_budget(self):
        value = batch(2)
        value["jobs"][1]["job"]["kind"] = "research.rank"
        client = Client(value)
        with patch.object(auxiliary, "emit"), self.assertRaises(auxiliary.ProtocolError):
            auxiliary.run_cycle(client, Worker(client, self.directory), Gate(), Stop())
        self.assertEqual([a for a, _ in client.calls], ["claim"])

    def test_legacy_transport_completes_one_job_without_extra_claims(self):
        response = {"state": "claimed", **entry(1)}
        client = Client(response)
        with patch.object(auxiliary, "emit"):
            self.assertEqual(auxiliary.run_cycle(client, Worker(client, self.directory), Gate(), Stop()), "completed")
        self.assertEqual([a for a, _ in client.calls], ["claim", "complete", "status"])

    def test_pressure_prevents_entire_batch_admission(self):
        for state in ("origin_pressure", "origin_unknown", "origin_warmup"):
            client = Client()
            with patch.object(auxiliary, "emit"):
                self.assertEqual(auxiliary.run_cycle(client, Worker(client, self.directory), Gate(state), Stop()), state)
            self.assertEqual(client.calls, [])

    def test_stop_after_receipt_does_not_claim_again(self):
        client, stop = Client(), Stop()
        client.after_status = lambda _: setattr(stop, "stopped", True)
        with patch.object(auxiliary, "emit"):
            self.assertEqual(auxiliary.run_cycle(client, Worker(client, self.directory), Gate(), stop), "completed")
        self.assertEqual(len(client.results), 1)
        self.assertEqual([a for a, _ in client.calls], ["claim", "complete", "status"])

    def test_lost_ack_is_replayed_with_identical_digest_before_next_claim(self):
        client = Client()
        client.fail_status_once = entry(0)["job"]["id"]
        worker = Worker(client, self.directory)
        with patch.object(auxiliary, "emit"), self.assertRaises(TransportError):
            auxiliary.run_cycle(client, worker, Gate(), Stop())
        self.assertEqual(len(client.results), 1)
        self.assertEqual(len(worker.journal.pending()), 1)
        before = client.results[entry(0)["job"]["id"]]["result_sha256"]
        client.calls.clear()
        with patch.object(auxiliary, "emit"):
            self.assertEqual(auxiliary.run_cycle(client, worker, Gate(), Stop()), "idle")
        self.assertEqual([a for a, _ in client.calls], ["complete", "status", "claim"])
        self.assertEqual(client.results[entry(0)["job"]["id"]]["result_sha256"], before)
        self.assertEqual(worker.journal.pending(), [])


if __name__ == "__main__":
    unittest.main()
