from pathlib import Path
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from msw_engine.chatgpt_export import import_zip, import_configured, trusted_url
from msw_engine.historical_learning import HistoricalLearning
from msw_engine.owner_context import seed


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.archive = self.root / 'export.zip'

    def fixture(self, name='conversations.json'):
        rows = [{'id':'conversation-1', 'title':'Project history', 'mapping':{
            'root':{'message':None},
            'u':{'message':{'author':{'role':'user'}, 'content':{'parts':['Build an application\napi_key: sk-' + 'A'*30]}}},
            'a':{'message':{'author':{'role':'assistant'}, 'content':{'parts':['Draft source specification']}}},
            'branch':{'message':{'author':{'role':'assistant'}, 'content':{'parts':['Another branch']}}}}}]
        with ZipFile(self.archive,'w') as z:
            z.writestr(name,json.dumps(rows))
            z.writestr('private-photo.png',b'not extracted')

    def test_import_preserves_branches_and_private_source_provenance(self):
        self.fixture()
        receipt = import_zip(self.archive,self.root,grant_sha256='known-grant')
        self.assertEqual(receipt['text_records'],3)
        self.assertTrue(receipt['private_database_readback_verified'])
        self.assertFalse(receipt['full_live_account_access'])
        self.assertFalse((self.root/'private-photo.png').exists())
        db_path = self.root/'chatgpt-imports'/(receipt['archive_sha256']+'.sqlite3')
        self.assertEqual(db_path.stat().st_mode & 0o777,0o600)
        with sqlite3.connect(db_path) as db:
            self.assertNotIn('sk-', '\n'.join(r[0] for r in db.execute('SELECT body FROM documents')))
        learner = HistoricalLearning(self.root,lambda x: None)
        self.assertTrue(learner.available_sources()[0][0].startswith('chatgpt-export:'))

    def test_restart_uses_existing_import_without_reusing_download_grant(self):
        self.fixture()
        url='https://chatgpt.com/export?signed=private-token'
        import_zip(self.archive,self.root,grant_sha256=hashlib.sha256(url.encode()).hexdigest())
        with patch.dict(os.environ,{'MSW_CHATGPT_EXPORT_URL':url}), patch('msw_engine.chatgpt_export.build_opener') as opener:
            self.assertEqual(import_configured(self.root)['state'],'imported_verified')
            opener.assert_not_called()

    def test_unknown_file_and_untrusted_urls_are_rejected(self):
        self.fixture('other.json')
        with self.assertRaises(ValueError):
            import_zip(self.archive,self.root)
        for url in ['http://chatgpt.com/x','https://openai.com.evil.invalid/export','https://127.0.0.1/export','https://name:password@chatgpt.com/export']:
            with self.assertRaises(ValueError):
                trusted_url(url)

    def test_context_survives_restart_and_invalid_digest_is_not_ingested(self):
        data={'schema':'msw.owner-context.v1','records':[{'id':'owner:test','title':'Owner project','text':'Continue this project and preserve work','provenance':'owner_supplied'}]}
        raw=json.dumps(data)
        env={'MSW_OWNER_CONTEXT_JSON':raw,'MSW_OWNER_CONTEXT_SHA256':hashlib.sha256(raw.encode()).hexdigest()}
        with patch.dict(os.environ,env):
            self.assertTrue(seed(self.root)['private_context_readback_verified'])
            self.assertEqual(seed(self.root)['owner_context_records'],1)
        with sqlite3.connect(self.root/'owner-packages-20261105'/'knowledge.sqlite3') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM documents').fetchone()[0],1)
        with patch.dict(os.environ,{**env,'MSW_OWNER_CONTEXT_SHA256':'0'*64}):
            with self.assertRaises(ValueError):
                seed(self.root)


if __name__=='__main__':
    unittest.main()
