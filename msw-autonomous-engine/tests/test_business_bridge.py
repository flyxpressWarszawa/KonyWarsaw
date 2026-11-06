from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from msw_engine.business_bridge import BusinessBridge
from msw_engine.business_knowledge import seed


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.client=type('Client',(),{'wordpress_url':'https://mobilny-serwis-warszawa.pl'})()

    def test_context_is_durable_and_publication_needs_qa_receipt(self):
        def remote(op,**fields):
            if op=='status': return {'owner_authorized':True,'paypal':{'enabled':True},'telegram':{}}
            if op=='context': return {'origin':self.client.wordpress_url,'records':[{'id':10,'title':'Service','excerpt':'Source content','url':self.client.wordpress_url+'/service'}],'next_offset':40}
            if op=='products': return {'records':[{'id':42,'status':'draft','qa_receipt_present':False}],'next_offset':None}
            if op=='capabilities': return {'capabilities':[]}
            raise AssertionError('Unreviewed product must not be published')
        call=Mock(side_effect=remote)
        result=BusinessBridge(self.client,self.root,remote=call).step()
        self.assertTrue(result['storage_readback_verified'])
        self.assertEqual(result['content_records_readback'],1)
        self.assertEqual(result['products_published_readback'],[])
        self.assertEqual(BusinessBridge(self.client,self.root,remote=call).position('content_offset'),40)

    def test_foreign_content_never_enters_memory(self):
        call=Mock(side_effect=[{'owner_authorized':True}, {'origin':'https://foreign.example','records':[]}])
        with self.assertRaises(ValueError):BusinessBridge(self.client,self.root,remote=call).step()
        self.assertFalse((self.root/'knowledge.sqlite3').exists())

    def test_knowledge_pack_readback_and_digest_boundary(self):
        pack={'schema':'msw.business-knowledge.v1','records':[{'id':'business:test','title':'Operator goal','text':'Continue authorized business work','approved':True,'provenance':'explicit_owner_instruction'}]}
        raw=json.dumps(pack); env={'MSW_BUSINESS_KNOWLEDGE_CHUNKS':'1','MSW_BUSINESS_KNOWLEDGE_00':raw,'MSW_BUSINESS_KNOWLEDGE_SHA256':hashlib.sha256(raw.encode()).hexdigest()}
        with patch.dict('os.environ',env):
            result=seed(self.root);self.assertEqual(result['records'],1);self.assertEqual(seed(self.root)['sha256'],result['sha256'])
        with patch.dict('os.environ',{**env,'MSW_BUSINESS_KNOWLEDGE_SHA256':'0'*64}):
            with self.assertRaises(ValueError):seed(self.root)


if __name__=='__main__':unittest.main()
