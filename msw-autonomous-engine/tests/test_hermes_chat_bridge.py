"""Offline state-machine tests: Hermes -> mailbox -> ChatGPT Work -> Hermes ACK."""
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from msw_engine.hermes_chat_bridge import SCHEMA, handle, MAX_ROUNDS
from msw_engine.tasks import TaskError, handlers

def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = "msw-owner-task-0001"
        self.conv = "msw-conv-20261114"
        self.sub = dict(schema=SCHEMA, operation="submit", task_id=self.key,
                        conversation_key=self.conv, kind="plan", goal="Przygotuj plan MSW.")

    def command(self, **kwargs):
        return handle(kwargs, self.root)

    def submit(self, **kw):
        return self.command(**{**self.sub, **kw})

    def claim(self):
        return self.command(schema=SCHEMA, operation="claim", executor="chatgpt_work")

    def report(self, claimed, answer="Wykonałem analizę.", outcome="completed", **kw):
        data=dict(schema=SCHEMA,operation="report",task_id=claimed["task_id"],
                  lease_token=claimed["lease_token"],result=answer,
                  result_sha256=sha(answer),outcome=outcome)
        return self.command(**{**data,**kw})

    def acknowledge(self, task_id, result):
        return self.command(schema=SCHEMA, operation="ack", task_id=task_id,
                            result_sha256=sha(result))

    def test_full_handshake(self):
        created = self.submit()
        self.assertEqual(created["state"], "queued")
        claimed = self.claim()
        self.assertEqual(claimed["goal"], "Przygotuj plan MSW.")
        receipt = self.report(claimed)
        self.assertEqual(receipt["state"], "reported")
        ack = self.acknowledge(self.key, "Wykonałem analizę.")
        self.assertEqual(ack["state"], "acknowledged")
        self.assertTrue(ack["hermes_ack_verified"])
        self.assertEqual(ack["result_sha256"], sha("Wykonałem analizę."))

    def test_submit_is_idempotent_and_rejects_conflicts(self):
        self.assertEqual(self.submit(),self.submit())
        with self.assertRaisesRegex(TaskError,"idempotency_conflict"):
            self.submit(goal="different")

    def test_one_open_task_per_conversation(self):
        self.submit()
        with self.assertRaisesRegex(TaskError,"conversation_busy"):
            self.submit(task_id="msw-owner-task-0002")

    def test_claim_never_reissues_in_flight(self):
        self.submit()
        one = self.claim()
        self.assertEqual(self.claim()["state"], "empty")
        self.assertEqual(one["state"],"leased")

    def test_invalid_proof_or_result_never_completes(self):
        self.submit()
        lease = self.claim()
        with self.assertRaisesRegex(TaskError,"lease_not_valid"):
            self.report(lease, lease_token="invalid-but-long-enough-123")
        with self.assertRaisesRegex(TaskError,"report_content"):
            self.report(lease,result_sha256="0" * 64)
        self.assertEqual(self.command(schema=SCHEMA,operation="status",task_id=self.key)["state"],"leased")

    def test_report_replay_with_same_digest_only(self):
        self.submit()
        lease=self.claim()
        r1=self.report(lease)
        self.assertEqual(r1,self.report(lease))
        with self.assertRaisesRegex(TaskError,"conflicting_result"):
            self.report(lease,answer="other text")

    def test_parent_must_be_acknowledged(self):
        self.submit()
        with self.assertRaisesRegex(TaskError,"conversation_busy"):
            self.submit(task_id="msw-owner-task-0002",parent_id=self.key)
        claimed=self.claim()
        self.report(claimed)
        with self.assertRaisesRegex(TaskError,"conversation_busy"):
            self.submit(task_id="msw-owner-task-0002",parent_id=self.key)
        self.acknowledge(self.key,"Wykonałem analizę.")
        child=self.submit(task_id="msw-owner-task-0002",parent_id=self.key,
                          goal="Zaplanuj kolejny krok.")
        self.assertEqual(child["depth"],2)
        self.assertEqual(child["parent_id"],self.key)

    def test_cannot_branch_from_same_parent(self):
        self.submit()
        lease=self.claim()
        self.report(lease)
        self.acknowledge(self.key,"Wykonałem analizę.")
        child="msw-owner-task-0002"
        self.submit(task_id=child,parent_id=self.key)
        lease=self.claim()
        self.report(lease)
        self.acknowledge(child,"Wykonałem analizę.")
        with self.assertRaisesRegex(TaskError,"parent_already_continued"):
            self.submit(task_id="msw-owner-task-0003",parent_id=self.key)

    def test_round_limit_prevents_infinite_loop(self):
        parent=None
        for n in range(MAX_ROUNDS):
            ident=f"msw-owner-task-{n:04d}"
            result=self.submit(task_id=ident, parent_id=parent)
            self.assertEqual(result["depth"],n+1)
            lease=self.claim()
            self.report(lease)
            self.acknowledge(ident,"Wykonałem analizę.")
            parent=ident
        with self.assertRaisesRegex(TaskError,"max_rounds"):
            self.submit(task_id="msw-owner-task-9999",parent_id=parent)

    def test_bad_schema_rejected(self):
        with self.assertRaisesRegex(TaskError,"schema"):
            self.command(schema="unknown",operation="status")

    def test_no_external_model_calls_when_inspecting(self):
        out=self.command(schema=SCHEMA,operation="status")
        self.assertFalse(out["live_external_chat_connected"])
        self.assertEqual(out["counts"],{})

    def test_worker_capability_flag_defaults_off(self):
        with patch.dict(os.environ, {"MSW_HERMES_CHAT_LOOP_ENABLED": "0"}):
            self.assertNotIn("hermes.chat.exchange",handlers())
        with patch.dict(os.environ, {"MSW_HERMES_CHAT_LOOP_ENABLED": "1"}):
            self.assertIn("hermes.chat.exchange",handlers())


if __name__ == "__main__":
    unittest.main()
