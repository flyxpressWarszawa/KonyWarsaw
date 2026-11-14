"""Offline state-machine tests for job and reply exchange."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from msw_engine.hermes_chat_bridge import HERMES_EXECUTOR, LEGACY_EXECUTOR, MAX_ROUNDS, SCHEMA, SCHEDULED_EXECUTOR, handle
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
        return self.command(schema=SCHEMA, operation="claim", executor=LEGACY_EXECUTOR)

    def claim_scheduled(self, conversation):
        return self.command(schema=SCHEMA, operation="claim", executor=SCHEDULED_EXECUTOR,
                            conversation_key=conversation)

    def claim_hermes(self):
        return self.command(schema=SCHEMA, operation="claim", executor=HERMES_EXECUTOR,
                            conversation_key=self.conv)

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

    def test_scheduled_claim_only_reads_its_conversation_and_executor(self):
        other_conv = "msw-conv-20261115"
        other_task = "msw-owner-task-0002"
        self.submit(executor=SCHEDULED_EXECUTOR)
        self.submit(task_id=other_task, conversation_key=other_conv, executor=SCHEDULED_EXECUTOR)
        self.assertEqual(self.claim()["state"], "empty")
        self.assertEqual(self.claim_scheduled("msw-conv-unknown")["state"], "empty")
        with self.assertRaisesRegex(TaskError, "claim_fields"):
            self.command(schema=SCHEMA, operation="claim", executor=SCHEDULED_EXECUTOR)
        claim = self.claim_scheduled(other_conv)
        self.assertEqual(claim["task_id"], other_task)
        self.assertEqual(claim["executor"], SCHEDULED_EXECUTOR)
        self.report(claim)
        ack = self.acknowledge(other_task, "Wykonałem analizę.")
        self.assertEqual(ack["result"], "Wykonałem analizę.")
        next_task = "msw-owner-task-0003"
        self.submit(task_id=next_task, conversation_key=other_conv, parent_id=other_task,
                    executor=SCHEDULED_EXECUTOR, goal=ack["result"] + " Przygotuj kolejny krok.")
        self.assertEqual(self.claim_scheduled(other_conv)["task_id"], next_task)
        self.assertEqual(self.claim_scheduled(self.conv)["task_id"], self.key)

    def test_legacy_queue_rows_survive_schema_upgrade(self):
        queued = self.submit()
        database = self.root / "hermes-chat-loop" / "exchange.sqlite3"
        with closing(sqlite3.connect(database)) as db:
            db.execute("ALTER TABLE jobs DROP COLUMN executor")
        self.assertEqual(self.submit(), queued)
        self.assertEqual(self.claim_scheduled(self.conv)["state"], "empty")
        self.assertEqual(self.claim()["task_id"], self.key)

    def test_executor_cannot_change_on_retry_or_follow_up(self):
        self.submit(executor=SCHEDULED_EXECUTOR)
        with self.assertRaisesRegex(TaskError, "idempotency_conflict"):
            self.submit()
        self.report(self.claim_scheduled(self.conv))
        self.acknowledge(self.key, "Wykonałem analizę.")
        with self.assertRaisesRegex(TaskError, "parent_executor_mismatch"):
            self.submit(task_id="msw-owner-task-0002", parent_id=self.key)

    def test_same_work_across_executors_is_assigned_once(self):
        work = "msw-owner-work-0001"
        first = self.submit(executor=SCHEDULED_EXECUTOR, work_key=work)
        replay = self.submit(task_id="msw-owner-task-0002", executor=SCHEDULED_EXECUTOR, work_key=work)
        self.assertEqual(first, replay)
        self.assertEqual(first["work_key"], work)
        self.assertEqual(first["executor"], SCHEDULED_EXECUTOR)
        with self.assertRaisesRegex(TaskError, "work_already_assigned"):
            self.submit(task_id="msw-owner-task-0003", executor=HERMES_EXECUTOR, work_key=work)
        self.assertEqual(self.claim_hermes()["state"], "empty")

    def test_concurrent_assignment_replays_original_task_id(self):
        def assign(task_id):
            return self.submit(task_id=task_id, executor=SCHEDULED_EXECUTOR,
                               work_key="msw-owner-work-0001")

        self.claim_scheduled(self.conv)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.map(assign, [self.key, "msw-owner-task-0002"])
        self.assertEqual(a, b)
        self.assertEqual(self.command(schema=SCHEMA, operation="status")["counts"], {"queued": 1})

    def test_concurrent_claims_return_one_lease(self):
        self.submit(executor=SCHEDULED_EXECUTOR, work_key="msw-owner-work-0001")
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(self.claim_scheduled, [self.conv, self.conv]))
        self.assertEqual(sorted(claim["state"] for claim in claims), ["empty", "leased"])

    def test_hermes_can_take_next_work_in_same_chat_after_ack(self):
        self.submit(executor=SCHEDULED_EXECUTOR, work_key="msw-owner-work-0001")
        self.report(self.claim_scheduled(self.conv))
        next_work = dict(task_id="msw-owner-task-0002", parent_id=self.key,
                         work_key="msw-owner-work-0002", executor=HERMES_EXECUTOR, start_next=True)
        with self.assertRaisesRegex(TaskError, "conversation_busy"):
            self.submit(**next_work)
        self.acknowledge(self.key, "Wykonałem analizę.")
        next_task = self.submit(**next_work)
        self.assertEqual(next_task["depth"], 1)
        self.assertEqual(self.claim_scheduled(self.conv)["state"], "empty")
        self.assertEqual(self.claim_hermes()["task_id"], next_task["task_id"])

    def test_blocked_work_cannot_start_next_as_completed(self):
        self.submit(executor=SCHEDULED_EXECUTOR, work_key="msw-owner-work-0001")
        self.report(self.claim_scheduled(self.conv), outcome="blocked")
        self.acknowledge(self.key, "Wykonałem analizę.")
        with self.assertRaisesRegex(TaskError, "parent_not_completed"):
            self.submit(task_id="msw-owner-task-0002", parent_id=self.key,
                        work_key="msw-owner-work-0002", executor=HERMES_EXECUTOR, start_next=True)

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

    def test_github_backend_requires_explicit_selection(self):
        request = dict(schema=SCHEMA, operation="status")
        with patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "unsupported"}):
            with self.assertRaisesRegex(TaskError, "backend_configuration"):
                self.command(**request)

    def test_github_backend_delegates_only_when_selected(self):
        request = dict(schema=SCHEMA, operation="status")
        with patch.dict(os.environ, {"MSW_HERMES_CHAT_BACKEND": "github"}):
            with patch("msw_engine.github_chat_queue.handle", return_value={"github_queue_connected": True}) as remote:
                result = self.command(**request)
        self.assertTrue(result["github_queue_connected"])
        remote.assert_called_once()


if __name__ == "__main__":
    unittest.main()
