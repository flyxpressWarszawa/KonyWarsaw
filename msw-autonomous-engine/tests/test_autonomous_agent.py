from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from unittest.mock import Mock

from msw_engine.autonomous_agent import AutonomousAgent, status


def answer(payload):
    text = 'Specyfikacja produktu i plan testów. ' * 20
    return {'text': text, 'answer_sha256': hashlib.sha256(text.encode()).hexdigest(), 'model': 'test-model'}


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.now = [1800000000]
        self.generator = Mock(side_effect=answer)
        self.search = Mock(return_value={'items': []})

    def agent(self, **kwargs):
        return AutonomousAgent(self.directory, generate=self.generator, search=self.search, clock=lambda: self.now[0], **kwargs)

    def test_restart_does_not_repeat_paid_work_in_same_hour(self):
        first = self.agent().step('business')
        self.assertEqual(first['state'], 'completed')
        self.assertTrue(first['storage_readback_verified'])
        self.assertEqual(self.agent().step('business')['state'], 'waiting')
        self.generator.assert_called_once()
        self.now[0] += 3600
        self.assertEqual(self.agent().step('business')['state'], 'completed')
        self.assertEqual(self.generator.call_count, 2)

    def test_timeout_resumes_same_payload_even_in_next_hour(self):
        self.generator.side_effect = [TimeoutError('private server details'), answer({})]
        self.assertEqual(self.agent().step('business')['state'], 'retry')
        original = self.generator.call_args.args[0]
        self.now[0] += 3600
        self.assertEqual(self.agent().step('business')['state'], 'completed')
        self.assertEqual(self.generator.call_args.args[0], original)
        self.search.assert_called_once()

    def test_backoff_blocks_immediate_repeated_generation(self):
        self.generator.side_effect = TimeoutError()
        self.assertEqual(self.agent().step('business')['state'], 'retry')
        self.assertEqual(self.agent().step('business')['state'], 'waiting')
        self.generator.assert_called_once()

    def test_connections_are_fixed_signed_reads_and_false_is_preserved(self):
        def inspect(payload):
            return {'inspections': [{'engine': name, 'native_read_verified': name != 'v7'} for name in payload['engine_link']['engines']]}
        probe = Mock(side_effect=inspect)
        result = self.agent(inspect=probe).step('connections')
        self.assertEqual(result['verified_native_reads'], 4)
        self.assertEqual(probe.call_count, 2)
        self.generator.assert_not_called()

    def test_connection_failure_does_not_block_business(self):
        agent = self.agent(inspect=Mock(side_effect=TimeoutError()))
        self.assertEqual(agent.step('connections')['state'], 'retry')
        self.assertEqual(agent.step('business')['state'], 'completed')

    def test_shutdown_does_not_call_model(self):
        self.assertEqual(self.agent().step('business', stopping=lambda: True)['state'], 'waiting')
        self.generator.assert_not_called()

    def test_answer_integrity_is_required(self):
        self.generator.side_effect = None
        self.generator.return_value = {**answer({}), 'answer_sha256': '0' * 64}
        self.assertEqual(self.agent().step('business')['state'], 'retry')
        with self.agent().connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs WHERE state='completed'").fetchone()[0], 0)

    def test_private_report_requires_explicit_request_and_stays_unverified(self):
        agent = self.agent()
        agent.step('business')
        agent.heartbeat()
        public_status = status(self.directory)
        self.assertNotIn('latest_business_report', public_status)
        report = status(self.directory, include_report=True)['latest_business_report']
        self.assertEqual(report['classification'], 'ai_business_proposal_requires_fact_review')
        self.assertFalse(report['demand_verified'])
        self.assertFalse(report['publication_performed'])
        self.assertFalse((self.directory / 'knowledge.sqlite3').exists())
        self.assertNotIn(report['text'], json.dumps(public_status))

    def test_cannot_supply_arbitrary_agent_action(self):
        with self.assertRaises(ValueError):
            self.agent().step('shell')

    def test_known_transport_error_code_is_logged_without_arbitrary_details(self):
        from msw_engine.tasks import TaskError
        self.generator.side_effect = TaskError('fed_generate_http_503:provider_unavailable')
        self.assertEqual(self.agent().step('business')['code'], 'fed_generate_http_503:provider_unavailable')
        self.now[0] += 3600
        self.generator.side_effect = TaskError('customer content / secret token')
        self.assertEqual(self.agent().step('business')['code'], 'TaskError')

    def test_terminal_uncertainty_does_not_rebill_or_block_future_topics(self):
        from msw_engine.tasks import TaskError
        self.generator.side_effect = TaskError('fed_generate_http_502:mswf_model_msw_brain_uncertain')
        self.assertEqual(self.agent().step('business')['state'], 'blocked_provider_outcome_unknown')
        self.assertEqual(self.agent().step('business')['state'], 'waiting')
        self.generator.assert_called_once()
        self.now[0] += 3600
        self.generator.side_effect = answer
        self.assertEqual(self.agent().step('business')['state'], 'completed')

    def test_continuation_survives_restart_and_does_not_claim_full_autonomy(self):
        def inspect(payload):
            return {'inspections': [{'engine': n, 'native_read_verified': n != 'v5', 'state': 'checked'} for n in payload['engine_link']['engines']]}
        first = self.agent(inspect=inspect)
        first.step('connections')
        self.assertEqual(self.agent().continuation()['pending_engines'], ['v5'])
        self.now[0] += 900
        second = self.agent(inspect=lambda p: {'inspections': [{'engine': n, 'native_read_verified': True} for n in p['engine_link']['engines']]})
        second.step('connections')
        plan = second.continuation()
        self.assertEqual(plan['pending_engines'], [])
        self.assertEqual(plan['state'], 'execution_verification_pending')
        self.assertFalse(plan['all_engines_autonomous_verified'])
        self.assertTrue(plan['inactive_plugins_must_remain_inactive'])

    def test_transport_failure_preserves_pending_work(self):
        agent = self.agent(inspect=Mock(side_effect=TimeoutError()))
        self.assertEqual(agent.step('connections')['state'], 'retry')
        self.assertEqual(self.agent().continuation()['state'], 'transport_retry')
        self.assertFalse(self.agent().continuation()['all_engines_autonomous_verified'])

    def test_history_learning_is_separate_from_business_generation(self):
        agent = self.agent()
        result = agent.step('learning')
        self.assertEqual(result['state'], 'completed')
        self.assertNotIn('business_brief_created', result)
        self.generator.assert_not_called()


if __name__ == '__main__':
    unittest.main()
