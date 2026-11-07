import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from msw_engine.openai_direct import generate
from msw_engine.tasks import TaskError


class Response:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, _): return json.dumps({'status': 'completed', 'output_text': 'Sprawdź dostępność hosta.'}).encode()


class OpenAITests(unittest.TestCase):
    def test_direct_paid_request_is_recorded_and_replayed_without_duplicate(self):
        calls = []
        class Opener:
            def open(self, req, timeout): calls.append((req.full_url, timeout)); return Response()
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'OPENAI_API_KEY': 'K'*40, 'MSW_OPENAI_MODEL': 'test-model'}), \
             patch('msw_engine.openai_direct.build_opener', return_value=Opener()):
            payload = {'text': 'Ocena połączenia.', 'request_id': 'doctor-failover:1234567890abcdef', 'max_tokens': 400}
            a = generate(payload, Path(root)); b = generate(payload, Path(root))
            self.assertEqual(a, b); self.assertEqual(len(calls), 1); self.assertTrue(a['api_text_verified'])
            self.assertEqual(calls[0][0], 'https://api.openai.com/v1/responses')
            with self.assertRaises(TaskError): generate({**payload, 'text': 'Inne dane'}, Path(root))

    def test_uncertain_paid_response_is_not_silently_retried(self):
        calls = []
        class Opener:
            def open(self, req, timeout): calls.append(1); raise URLError('timeout')
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'OPENAI_API_KEY': 'K'*40, 'MSW_OPENAI_MODEL': 'test-model'}), \
             patch('msw_engine.openai_direct.build_opener', return_value=Opener()):
            payload = {'text': 'Ocena połączenia.', 'request_id': 'doctor-failover:1234567890abcdef', 'max_tokens': 400}
            with self.assertRaises(TaskError): generate(payload, Path(root))
            with self.assertRaises(TaskError): generate(payload, Path(root))
            self.assertEqual(len(calls), 1)


if __name__ == '__main__': unittest.main()
