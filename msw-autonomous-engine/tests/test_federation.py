import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from msw_engine.federation import gateway, generate
from msw_engine.tasks import TaskError, knowledge_ingest


class FederationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def fake_gateway(self, operation, values=None):
        if operation == 'snapshot':
            return {'wordpress': {'installed': 286, 'active': 272}, 'python_hub': {'reachable': True}}
        if operation == 'public_context':
            return {'items': [{'source_url': 'https://mobilny-serwis-warszawa.pl/', 'title': 'Spawanie', 'excerpt': 'Katalog publiczny'}]}
        if operation == 'python_advice':
            return {'state': 'native_text_received', 'text': 'Propozycja serwera Python.'}
        self.sent = values
        return {'text': 'MSW współpracuje przez wspólną kolejkę.', 'model': 'gpt-5-mini'}

    def test_three_systems_contribute_and_answer_digest_is_real(self):
        knowledge_ingest({'records': [{'id': 'approved:one', 'title': 'Spawanie', 'text': 'Usługi spawania MSW', 'approved': True}]}, self.directory)
        with patch('msw_engine.federation.gateway', side_effect=self.fake_gateway):
            result = generate({'prompt': 'Spawanie MSW', 'task': 'business'}, self.directory)
        self.assertEqual(result['answer_sha256'], hashlib.sha256(result['text'].encode()).hexdigest())
        self.assertEqual(len(result['contributors']), 6)
        self.assertEqual(len(result['sources']), 2)
        self.assertIn('Katalog publiczny', self.sent['text'])
        self.assertIn('Usługi spawania MSW', self.sent['text'])
        self.assertIn('Propozycja serwera Python.', self.sent['text'])
        self.assertFalse(result['publication_performed'])

    def test_opt_out_does_not_read_other_systems_or_memory(self):
        with patch('msw_engine.federation.gateway', side_effect=self.fake_gateway) as call:
            result = generate({'prompt': 'Test', 'use_knowledge': False, 'collaborate': False}, self.directory)
        self.assertEqual([c.args[0] for c in call.call_args_list], ['generate'])
        self.assertEqual(result['sources'], [])

    def test_invalid_arguments_are_rejected_before_any_remote_call(self):
        for changed in ({'base_url': 'https://other.invalid'}, {'task': 'shell'}, {'max_tokens': True}, {'max_tokens': 3001},
                        {'use_knowledge': 'false'}, {'collaborate': 1}, {'prompt': 'x'*6001}, {'corpus': 'owner_packages_20261105'}):
            with self.subTest(changed=changed), patch('msw_engine.federation.gateway') as call:
                with self.assertRaises(TaskError): generate(dict({'prompt': 'Test'}, **changed), self.directory)
                call.assert_not_called()

    def test_supplemental_context_is_bounded_without_truncating_question(self):
        def huge(operation, values=None):
            if operation == 'public_context': return {'items': [{'source_url':'https://mobilny-serwis-warszawa.pl/', 'title': 'T', 'excerpt': 'ż'*10000}]}
            return self.fake_gateway(operation, values)
        prompt = 'Pytanie '+('ą'*2500)
        with patch('msw_engine.federation.gateway', side_effect=huge): generate({'prompt': prompt}, self.directory)
        self.assertLessEqual(len(self.sent['text'].encode()), 10000)
        self.assertTrue(self.sent['text'].endswith(prompt))

    def test_empty_provider_text_is_not_reported_as_success(self):
        with patch('msw_engine.federation.gateway', return_value={'text': '', 'model': 'gpt-5-mini'}):
            with self.assertRaises(TaskError): generate({'prompt': 'Test','collaborate':False,'use_knowledge':False}, self.directory)

    def test_source_contacts_do_not_disable_the_existing_router_guard(self):
        def contacts(operation, values=None):
            if operation == 'python_advice': return {'state':'native_text_received','text':'Kontakt 570 933 114 lub biuro@example.com'}
            return self.fake_gateway(operation,values)
        with patch('msw_engine.federation.gateway',side_effect=contacts): generate({'prompt':'Spawanie MSW'},self.directory)
        self.assertNotIn('570 933 114',self.sent['text'])
        self.assertNotIn('biuro@example.com',self.sent['text'])

    def test_fixed_same_origin_gateway_and_signing(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): return json.dumps({'schema':'msw.federation.v1','ok':True,'data':{'verified':True}}).encode()
        with patch.dict(os.environ, {'MSW_WP_URL':'https://mobilny-serwis-warszawa.pl','MSW_NODE':'test-node','MSW_SECRET':'s'*64}), patch('msw_engine.federation.build_opener') as opener:
            opener.return_value.open.return_value = Response()
            self.assertTrue(gateway('snapshot')['verified'])
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, 'https://mobilny-serwis-warszawa.pl/wp-json/msw-federation/v1/gateway')
            self.assertTrue(any(k.lower()=='x-mswue-signature' for k in request.headers))
            self.assertNotIn('s'*64, request.data.decode())

    def test_redirect_or_provider_failure_never_exposes_response_body(self):
        with patch.dict(os.environ, {'MSW_WP_URL':'https://mobilny-serwis-warszawa.pl','MSW_NODE':'test-node','MSW_SECRET':'s'*64}), patch('msw_engine.federation.build_opener') as opener:
            opener.return_value.open.side_effect=HTTPError('https://other.invalid',302,'secret-body',{},None)
            with self.assertRaisesRegex(TaskError,'^federation_http_302$'):gateway('snapshot')

    def test_credential_bearing_or_insecure_url_refused(self):
        for url in ['http://host.invalid','https://user:pass@host.invalid','https://host.invalid/?secret=x']:
            with patch.dict(os.environ,{'MSW_WP_URL':url,'MSW_SECRET':'s'*64}), patch('msw_engine.federation.build_opener') as opener:
                with self.assertRaises(TaskError):gateway('snapshot')
                opener.assert_not_called()


if __name__=='__main__':unittest.main()
