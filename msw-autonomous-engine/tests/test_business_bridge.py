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
            if op=='knowledge': return {'state':'not_configured'}
            raise AssertionError('Unreviewed product must not be published')
        call=Mock(side_effect=remote)
        result=BusinessBridge(self.client,self.root,remote=call).step()
        self.assertTrue(result['storage_readback_verified'])
        self.assertEqual(result['content_records_readback'],1)
        self.assertEqual(result['products_published_readback'],[])
        self.assertEqual(BusinessBridge(self.client,self.root,remote=call).position('content_offset'),40)

    def test_foreign_content_never_enters_memory(self):
        call=Mock(side_effect=[{'owner_authorized':True}, {'state':'not_configured'}, {'origin':'https://foreign.example','records':[]}])
        with self.assertRaises(ValueError):BusinessBridge(self.client,self.root,remote=call).step()
        self.assertFalse((self.root/'knowledge.sqlite3').exists())

    def test_knowledge_pack_readback_and_digest_boundary(self):
        pack={'schema':'msw.business-knowledge.v1','records':[{'id':'business:test','title':'Operator goal','text':'Continue authorized business work','approved':True,'provenance':'explicit_owner_instruction'}]}
        raw=json.dumps(pack); env={'MSW_BUSINESS_KNOWLEDGE_CHUNKS':'1','MSW_BUSINESS_KNOWLEDGE_00':raw,'MSW_BUSINESS_KNOWLEDGE_SHA256':hashlib.sha256(raw.encode()).hexdigest()}
        with patch.dict('os.environ',env):
            result=seed(self.root);self.assertEqual(result['records'],1);self.assertEqual(seed(self.root)['sha256'],result['sha256'])
        with patch.dict('os.environ',{**env,'MSW_BUSINESS_KNOWLEDGE_SHA256':'0'*64}):
            with self.assertRaises(ValueError):seed(self.root)

    def test_customer_welcome_is_bound_and_never_generates_or_resends(self):
        item={'id':'tg-update-1','input_sha256':'a'*64,'type':'welcome','text':'/ai'}
        def remote(op,**fields):
            if op=='telegram.inbox':return {'items':[item]}
            self.assertEqual(op,'telegram.reply');self.assertEqual(fields['conversation_id'],item['id'])
            self.assertNotIn('chat_id',fields)
            return {'state':'sent','delivery_readback_verified':True}
        call=Mock(side_effect=remote);model=Mock(side_effect=AssertionError('Welcome requires no paid API'))
        b=BusinessBridge(self.client,self.root,remote=call)
        self.assertEqual(b.customer_step(generate=model)['customer_replies_readback'],1)
        self.assertEqual(b.customer_step(generate=model)['customer_replies_readback'],0)
        self.assertEqual(sum(c.args[0]=='telegram.reply' for c in call.call_args_list),1)

    def test_customer_model_failure_uses_fixed_response_without_new_paid_request(self):
        item={'id':'tg-update-2','input_sha256':'b'*64,'type':'question','text':'Czy mogę zamówić usługę?'}
        def remote(op,**fields):
            if op=='telegram.inbox':return {'items':[item]}
            self.assertIn('/zgloszenie',fields['reply']);return {'state':'sent','delivery_readback_verified':True}
        model=Mock(side_effect=ValueError('native_bridge_terminal_failure:outcome_unknown'))
        bridge=BusinessBridge(self.client,self.root,remote=remote)
        self.assertEqual(bridge.customer_step(generate=model)['customer_replies_readback'],1)
        bridge.customer_step(generate=model);self.assertEqual(model.call_count,1)

    def test_model_link_is_rejected_and_source_prompt_is_durable(self):
        item={'id':'tg-update-3','input_sha256':'c'*64,'type':'question','text':'Kupno produktu'}
        replies=[]
        def remote(op,**fields):
            if op=='telegram.inbox':return {'items':[item]}
            replies.append(fields['reply']);return {'state':'sent','delivery_readback_verified':True}
        model=Mock(return_value={'text':'Zapłać tutaj https://foreign.example/payment'})
        BusinessBridge(self.client,self.root,remote=remote).customer_step(generate=model)
        self.assertNotIn('foreign.example',replies[0]);self.assertIn('/zgloszenie',replies[0])

    def test_private_owner_knowledge_is_authenticated_hashed_and_read_back(self):
        raw=json.dumps([{'id':'business:owner:test','approved':True,'title':'Cel MSW','text':'Rozwijaj istniejący biznes','provenance':'owner_instruction'}])
        page={'state':'ready','origin':self.client.wordpress_url,'private_owner_authorized':True,
            'source_sha256':'d'*64,'total':1,'offset':0,'next_offset':None,'payload_json':raw,
            'payload_sha256':hashlib.sha256(raw.encode()).hexdigest()}
        call=Mock(return_value=page);b=BusinessBridge(self.client,self.root,remote=call)
        result=b.sync_owner_knowledge();self.assertEqual(result['records_readback_verified'],1)
        self.assertEqual(b.sync_owner_knowledge(),result)
        bad={**page,'source_sha256':'e'*64,'payload_sha256':'0'*64};call.return_value=bad
        with self.assertRaises(ValueError):b.sync_owner_knowledge()

    def test_central_adapter_upgrade_does_not_suspend_other_lanes(self):
        call=Mock(side_effect=ValueError('business_http_400:msw_business_agent_operation'))
        result=BusinessBridge(self.client,self.root,remote=call).sync_owner_knowledge()
        self.assertEqual(result['state'],'awaiting_central_knowledge_adapter')

    def test_enabled_native_factory_is_queued_without_claiming_delivery(self):
        def remote(op,**fields):
            if op=='status': return {'owner_authorized':True,'paypal':{},'telegram':{}}
            if op=='knowledge': return {'state':'not_configured'}
            if op=='context': return {'origin':self.client.wordpress_url,'records':[],'next_offset':None}
            if op=='products': return {'records':[],'next_offset':None}
            if op=='capabilities': return {'catalog_sha256':'f'*64,'capabilities':[{'id':'director.factory','enabled':True}]}
            if op=='capability.execute':
                self.assertEqual(fields['capability'],'director.factory')
                return {'state':'completed','result':{'state':'native_job_pending','job_readback_verified':True,'native_action_completed':False,'product_delivery_verified':False}}
            raise AssertionError(op)
        answer=BusinessBridge(self.client,self.root,remote=remote).step()
        self.assertEqual(answer['factory']['state'],'native_job_pending')
        self.assertFalse(answer['factory']['native_action_completed'])
        self.assertFalse(answer['factory']['product_delivery_verified'])


if __name__=='__main__':unittest.main()
