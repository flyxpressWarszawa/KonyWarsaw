from pathlib import Path
import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from msw_engine.runtime_bridge import generate, inspect, call
from msw_engine.tasks import TaskError
from msw_engine.federation import generate as federated_generate


class RuntimeBridgeTest(unittest.TestCase):
    def test_generation_requires_same_request_and_independent_readback(self):
        values = {'request_id': 'stable-paid-id'}
        text = 'MSW-BRIDGE-OK'
        good = {'request_id': values['request_id'], 'state': 'completed', 'text': text,
                'answer_sha256': hashlib.sha256(text.encode()).hexdigest(), 'storage_readback_verified': True}
        with patch('msw_engine.runtime_bridge.call', return_value=good):
            self.assertEqual(generate(values)['text'], text)
        for change in ({'request_id': 'different'}, {'storage_readback_verified': False}, {'answer_sha256': '0'*64}):
            with patch('msw_engine.runtime_bridge.call', return_value={**good, **change}):
                with self.assertRaises(TaskError):
                    generate(values)

    def test_unknown_outcome_never_creates_another_paid_request(self):
        with patch('msw_engine.runtime_bridge.call', return_value={'state': 'provider_outcome_unknown', 'request_id': 'stable-paid-id'}) as remote:
            with self.assertRaises(TaskError):
                generate({'request_id': 'stable-paid-id'})
            self.assertEqual(remote.call_count, 1)

    def test_fallback_requires_definitive_failure_configured_provider_and_policy(self):
        values = {'request_id': 'stable-paid-id', 'task': 'review', 'text': 'Synthetic test', 'max_tokens': 1000}
        text = 'Secondary provider text'
        attempts = []
        def remote(operation, payload=None):
            if operation == 'status':
                return {'secondary_generation_allowed': True, 'existing_api_providers': {'groq': {'configured': True}}}
            attempts.append(payload)
            if len(attempts) == 1:
                return {'state': 'provider_failed', 'request_id': values['request_id'], 'error_code': 'msw_openai_http'}
            return {'state': 'completed', 'request_id': payload['request_id'], 'text': text,
                    'answer_sha256': hashlib.sha256(text.encode()).hexdigest(), 'storage_readback_verified': True, 'provider': 'groq'}
        with patch('msw_engine.runtime_bridge.call', side_effect=remote):
            self.assertEqual(generate(values, allow_fallback=True)['provider'], 'groq')
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[1]['provider'], 'groq')
        self.assertNotEqual(attempts[1]['request_id'], values['request_id'])
        self.assertEqual(attempts[1]['text'], values['text'])

    def test_arbitrary_operation_and_unsafe_base_are_rejected(self):
        for base in ('http://example.invalid', 'https://name:password@example.invalid', 'https://example.invalid/?secret=x'):
            with patch.dict(os.environ, {'MSW_WP_URL': base, 'MSW_SECRET': 'a'*32}):
                with self.assertRaises(TaskError):
                    call('status')
        with patch.dict(os.environ, {'MSW_WP_URL': 'https://example.invalid', 'MSW_SECRET': 'a'*32}):
            with self.assertRaises(TaskError):
                call('arbitrary_admin_action')

    def test_inventory_is_persisted_but_inconsistent_counts_are_rejected(self):
        observation = {'plugins': [{'id': 'active/plugin.php', 'active': True}, {'id': 'inactive/plugin.php', 'active': False}],
                       'installed_plugins': 2, 'active_plugins': 1, 'paid_openai': {'key_configured': False},
                       'payment_gateways': [{'id': 'ppcp-gateway', 'enabled': True}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('msw_engine.runtime_bridge.call', return_value=observation):
                self.assertTrue(inspect(root)['paypal_gateway_enabled'])
            self.assertEqual(json.loads((root/'autonomous-agent/runtime-bridge.json').read_text()), observation)
            self.assertEqual((root/'autonomous-agent/runtime-bridge.json').stat().st_mode & 0o777, 0o600)
            with patch('msw_engine.runtime_bridge.call', return_value={**observation, 'active_plugins': 2}):
                with self.assertRaises(ValueError):
                    inspect(root)

    def test_transport_is_pinned_per_request_across_configuration_change(self):
        payload = {'prompt': 'Prepare a short MSW product specification.', 'request_id': 'old-persisted-request',
                   'collaborate': False, 'use_knowledge': False, 'task': 'business'}
        answer = {'text': 'Verified native provider text.', 'model': 'fixture-model'}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {'MSW_PAID_AI_TRANSPORT': ''}), patch('msw_engine.federation.gateway', return_value=answer) as old:
                federated_generate(payload, root)
                self.assertEqual(old.call_count, 1)
            with patch.dict(os.environ, {'MSW_PAID_AI_TRANSPORT': 'native_runtime'}), patch('msw_engine.federation.gateway', return_value=answer) as old, patch('msw_engine.runtime_bridge.generate', return_value=answer) as native:
                federated_generate(payload, root)
                self.assertEqual(old.call_count, 1)
                native.assert_not_called()
                federated_generate({**payload, 'request_id': 'new-runtime-request'}, root)
                self.assertEqual(native.call_count, 1)


if __name__ == '__main__':
    unittest.main()
