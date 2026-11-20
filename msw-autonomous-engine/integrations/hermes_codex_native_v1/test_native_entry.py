"""Offline behavior tests. No native transport imports, credentials, or provider calls."""
import copy
import fcntl
import hashlib
import tempfile
import unittest
from pathlib import Path

import native_entry as n


def request():
    return {'schema': n.SCHEMA, 'idempotency_key': 'owner-test-1', 'prompt': 'Return HELLO.',
            'token_limit_policy': 'provider_default_no_hard_cap', 'origin': 'new_owner_task'}


class Fake:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.result = {'thread_id': 'thread-1', 'turn_id': 'turn-1', 'text': 'HELLO',
                       'error': False, 'interrupted': False, 'tool_iterations': 0, 'usage': {}}
        self.stored = {'thread': {'id': 'thread-1', 'model': n.MODEL, 'turns': [
            {'id': 'turn-1', 'status': 'completed', 'items': [
                {'type': 'userMessage', 'content': []}, {'type': 'agentMessage', 'text': 'HELLO'}]}]}}

    def __call__(self, directory):
        self.directory = directory
        self.calls.append('constructed')
        self.closed = False
        return self

    def check(self):
        self.calls.append('check')
        return {'type': 'chatgpt', 'plan': 'pro'}

    def start(self):
        self.calls.append('start')
        return 'thread-1'

    def run(self, prompt):
        state = n.read_json(self.directory / 'state.json')
        assert state['status'] == 'DISPATCH_INTENT' and state['thread_id'] == 'thread-1'
        assert n.read_json(self.directory / 'request.json')['prompt'] == prompt
        self.calls.append('run')
        if self.fail:
            raise TimeoutError('provider outcome is deliberately unknown')
        return copy.deepcopy(self.result)

    def close(self):
        self.closed = True

    def read(self, thread):
        assert self.closed, 'sender must close before independent readback'
        self.calls.append('read')
        return copy.deepcopy(self.stored)


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'runs'
        self.fake = Fake()
        self.entry = n.Entry(self.root, self.fake)
        self.req = request()

    def test_capped_business_unknown_and_unsafe_requests_never_reach_backend(self):
        changes = [{'schema': 'msw.engine.request.v1'}, {'origin': 'existing_queue_task'},
                   {'token_limit_policy': 'max_tokens'}, {'max_tokens': 10}, {'tools': []},
                   {'model': 'different-model'}, {'idempotency_key': '../escape'}, {'prompt': ''}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(n.Refused):
                self.entry.run({**self.req, **change})
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(self.root.exists())
        with self.assertRaises(n.Refused):
            n.parse('{"prompt":"first","prompt":"second"}')

    def test_same_body_returns_verified_cache_without_second_native_attempt(self):
        first = self.entry.run(self.req)
        second = self.entry.run(dict(reversed(list(self.req.items()))))
        self.assertEqual(first, second)
        self.assertEqual(first, self.entry.read(self.req['idempotency_key']))
        self.assertEqual(self.fake.calls, ['constructed', 'check', 'start', 'run', 'read'])
        self.assertEqual(first['status'], 'VERIFIED')
        self.assertEqual(first['response_sha256'], hashlib.sha256(b'HELLO').hexdigest())
        self.assertEqual(first['billing_mode'], 'chatgpt_account')

    def test_changed_prompt_conflicts_with_completed_or_unknown_request(self):
        self.entry.run(self.req)
        with self.assertRaisesRegex(n.Refused, 'IDEMPOTENCY_CONFLICT'):
            self.entry.run({**self.req, 'prompt': 'Different work'})
        self.assertEqual(self.fake.calls.count('run'), 1)

    def test_timeout_keeps_unknown_and_never_redispatches(self):
        self.fake.fail = True
        with self.assertRaisesRegex(n.Refused, 'ATTEMPT_INCOMPLETE_NO_REDISPATCH'):
            self.entry.run(self.req)
        self.assertEqual(self.entry.read(self.req['idempotency_key'])['status'], 'UNKNOWN')
        self.fake.fail = False
        with self.assertRaisesRegex(n.Refused, 'RESERVED_OR_UNKNOWN_NO_REDISPATCH'):
            self.entry.run(self.req)
        self.assertEqual(self.fake.calls.count('run'), 1)
        self.assertFalse((self.root / self.req['idempotency_key'] / 'receipt.json').exists())

    def test_reservation_crash_never_starts_backend(self):
        directory = self.root / self.req['idempotency_key']
        n.private(self.root); n.private(directory)
        n.save(directory / 'request.json', self.req)
        n.save(directory / 'state.json', {'status': 'RESERVED'})
        with self.assertRaisesRegex(n.Refused, 'RESERVED_OR_UNKNOWN_NO_REDISPATCH'):
            self.entry.run(self.req)
        self.assertEqual(self.fake.calls, [])

    def test_independent_readback_mismatches_remain_unknown(self):
        mutations = [lambda t: t.update(id='wrong'), lambda t: t.update(model='wrong'),
                     lambda t: t['turns'][0].update(id='wrong'),
                     lambda t: t['turns'][0].update(status='inProgress'),
                     lambda t: t['turns'][0]['items'][-1].update(text='different')]
        for i, mutate in enumerate(mutations):
            with self.subTest(case=i):
                fake = Fake(); mutate(fake.stored['thread'])
                entry = n.Entry(self.root / str(i), fake)
                with self.assertRaisesRegex(n.Refused, 'READBACK_'):
                    entry.run(self.req)
                with self.assertRaisesRegex(n.Refused, 'NO_REDISPATCH'):
                    entry.run(self.req)
                self.assertEqual(fake.calls.count('run'), 1)

    def test_tool_activity_cannot_produce_verified_receipt(self):
        self.fake.stored['thread']['turns'][0]['items'].append({'type': 'commandExecution'})
        with self.assertRaisesRegex(n.Refused, 'TOOL_EFFECT_REFUSED'):
            self.entry.run(self.req)
        self.assertEqual(self.entry.read(self.req['idempotency_key'])['status'], 'UNKNOWN')

    def test_cached_model_migration_or_corruption_never_reexecutes(self):
        receipt = self.entry.run(self.req)
        receipt['model'] = 'other-model'
        receipt.pop('receipt_sha256')
        receipt['receipt_sha256'] = n.digest(receipt)
        n.save(self.root / self.req['idempotency_key'] / 'receipt.json', receipt)
        with self.assertRaisesRegex(n.Refused, 'CACHED_RECEIPT_INVALID'):
            self.entry.run(self.req)
        self.assertEqual(self.fake.calls.count('run'), 1)

    def test_concurrent_request_lock_prevents_second_attempt(self):
        directory = self.root / self.req['idempotency_key']
        n.private(self.root); n.private(directory)
        lock_path = directory / 'lock'
        lock_path.touch(mode=0o600)
        with lock_path.open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(n.Refused, 'REQUEST_BUSY'):
                self.entry.run(self.req)
        self.assertEqual(self.fake.calls, [])


if __name__ == '__main__':
    unittest.main()
