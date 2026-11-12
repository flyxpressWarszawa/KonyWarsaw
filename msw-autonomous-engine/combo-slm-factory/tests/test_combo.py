"""Unit and contract tests only. No claim of live SLM, PayPal or satellite testing."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
from msw_combo_slm.core import (Brief, Store, Grant, Adapter, Orchestrator, FactoryError, STAGES,
    RELEASE_CHECKS, acceptance, design_candidate, contrast, digest, domain, similarity)
from msw_combo_slm.model import LocalSLM, validate

class ComboTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'data')
        self.brief = Brief('project01', 'example.com', 'Serwis techniczny', 'Właściciele firm', 'install01')
        self.store.create(self.brief)
        self.now = int(time.time())

    def tearDown(self): self.tmp.cleanup()

    def grant(self, b):
        return Grant(b.domain, self.now, True, True, False, frozenset(STAGES), 'verified-revision-1')

    def receipt(self, b, stage, rid):
        return dict(authenticated=True, verified=True, domain=b.domain, stage=stage,
                    request_id=rid, status='done', artifact_sha256=digest([stage,rid]))

    def adapter(self): return Adapter(self.receipt, lambda b,s,r,a: self.receipt(b,s,r))

    def evidence(self):
        def item(i, kind):
            out = dict(id=i+1, content_sha256=digest([kind,i]), url=f'https://example.com/{kind}/{i}',
                status='publish', public_readback=True, quality_review=True, unique_content=True,
                image_readback=True)
            if kind=='products': out.update(purchasable=True, delivery_verified=True, price_verified=True)
            return out
        return dict(authenticated=True, domain=self.brief.domain, observed_at=self.now,
                    checks={k:True for k in RELEASE_CHECKS}, portal_url='https://example.com/',
                    products=[item(i,'products') for i in range(50)], articles=[item(i,'articles') for i in range(30)])

    def test_domain_invalid(self):
        for name in ['http://evil.com','a.com/a','../evil.com','127.0.0.1','a..com','dom-ai.pl','a.com:88']:
            with self.subTest(name=name), self.assertRaises(FactoryError): domain(name)

    def test_minimum_product_count(self):
        with self.assertRaises(FactoryError): replace(self.brief, products=49)

    def test_minimum_article_count(self):
        with self.assertRaises(FactoryError): replace(self.brief, articles=29)

    def test_idempotency(self):
        self.assertFalse(self.store.create(self.brief)['created'])
        with self.assertRaises(FactoryError): self.store.create(replace(self.brief,business='Inny biznes'))

    def test_palette_contrast(self):
        for i in range(100):
            p=design_candidate(replace(self.brief,installation_id=f'install{i:04}'))['palette']
            self.assertGreaterEqual(contrast(p['primary'],p['on_primary']),4.5)
            self.assertGreaterEqual(contrast(p['background'],p['foreground']),4.5)

    def test_distinct_installations(self):
        designs=[]
        for i in range(41):
            b=replace(self.brief,project_id=f'project{i:04}',domain=f'site{i}.example.com',installation_id=f'install{i:04}')
            design=self.store.create(b)['design']
            for old in designs:
                self.assertLess(similarity(old,design),.86)
                self.assertNotEqual(old['palette']['primary'],design['palette']['primary'])
            designs.append(design)
        self.assertEqual(len({d['signature'] for d in designs}),41)

    def test_business_family(self):
        self.assertEqual(design_candidate(replace(self.brief,business='Jacuzzi i SPA'))['family'],'wellness')
        self.assertEqual(design_candidate(replace(self.brief,business='Portal edukacyjny'))['family'],'editorial')

    def test_memory_isolation(self):
        other=replace(self.brief,project_id='project02',installation_id='install02')
        self.store.create(other)
        self.store.remember('project01','doc1','Diagnostyka pomp basenowych','manual-1',approved=True)
        self.assertEqual(len(self.store.retrieve('project01','pompy diagnostyka')),1)
        self.assertEqual(self.store.retrieve('project02','pompy diagnostyka'),[])

    def test_memory_approval(self):
        with self.assertRaises(FactoryError): self.store.remember('project01','doc1','Tekst','src',approved=False)

    def test_memory_secrets(self):
        for text in ['password=123','api_key: secret','client_secret = xyz','Jan: jan@example.com']:
            with self.subTest(text=text), self.assertRaises(FactoryError): self.store.remember('project01','doc1',text,'src',approved=True)

    def test_missing_adapter(self):
        r=Orchestrator(self.store,self.grant,{}).tick('project01')
        self.assertEqual(r['state'],'adapter_missing')
        self.assertFalse(r['executed'])

    def test_unknown_adapter(self):
        with self.assertRaises(FactoryError): Orchestrator(self.store,self.grant,{'shell':self.adapter()})

    def test_quarantine_blocks(self):
        f=Orchestrator(self.store,lambda b: replace(self.grant(b),quarantined=True),{'domain.verify':self.adapter()})
        self.assertFalse(f.tick('project01')['executed'])

    def test_stale_grant_blocks(self):
        f=Orchestrator(self.store,lambda b: replace(self.grant(b),observed_at=self.now-901),{'domain.verify':self.adapter()})
        self.assertFalse(f.tick('project01')['executed'])

    def test_unapproved_domain_blocks(self):
        f=Orchestrator(self.store,lambda b: replace(self.grant(b),host='other.com'),{'domain.verify':self.adapter()})
        self.assertFalse(f.tick('project01')['executed'])

    def test_missing_scope_blocks(self):
        f=Orchestrator(self.store,lambda b: replace(self.grant(b),allowed_operations=frozenset()),{'domain.verify':self.adapter()})
        self.assertFalse(f.tick('project01')['executed'])

    def test_one_step_per_tick(self):
        f=Orchestrator(self.store,self.grant,{s:self.adapter() for s in STAGES})
        r=f.tick('project01')
        self.assertEqual(r['stage'],'domain.verify')
        self.assertEqual(sum(x['state']=='done' for x in self.store.status('project01')['jobs']),1)

    def test_unknown_outcome_not_retried(self):
        run=Mock(side_effect=TimeoutError('no response'))
        f=Orchestrator(self.store,self.grant,{'domain.verify':Adapter(run,lambda *a:{})})
        self.assertEqual(f.tick('project01')['state'],'reconcile')
        f.tick('project01')
        self.assertEqual(run.call_count,1)

    def test_queued_not_done(self):
        adapter=Adapter(lambda b,s,r:{**self.receipt(b,s,r),'status':'pending'},lambda *a:{})
        self.assertEqual(Orchestrator(self.store,self.grant,{'domain.verify':adapter}).tick('project01')['state'],'reconcile')

    def test_wrong_readback_rejected(self):
        adapter=Adapter(self.receipt,lambda b,s,r,a:{**self.receipt(b,s,r),'artifact_sha256':'a'*64})
        self.assertEqual(Orchestrator(self.store,self.grant,{'domain.verify':adapter}).tick('project01')['state'],'reconcile')

    def test_reconcile_without_replay(self):
        run=Mock(side_effect=TimeoutError())
        adapter=Adapter(run,lambda b,s,r,a:self.receipt(b,s,r))
        f=Orchestrator(self.store,self.grant,{'domain.verify':adapter})
        f.tick('project01')
        self.assertFalse(f.reconcile('project01')['write_replayed'])
        self.assertEqual(run.call_count,1)

    def test_inflight_not_replayed_after_restart(self):
        with self.store.connect() as db: db.execute("UPDATE jobs SET state='inflight' WHERE stage='domain.verify'")
        f=Orchestrator(Store(self.root/'data'),self.grant,{'domain.verify':self.adapter()})
        self.assertFalse(f.tick('project01')['executed'])

    def test_complete_receipts_not_ready(self):
        f=Orchestrator(self.store,self.grant,{s:self.adapter() for s in STAGES}, verifier=lambda b:self.evidence())
        for _ in STAGES: f.tick('project01')
        self.assertTrue(self.store.status('project01')['workflow_receipts_complete'])
        self.assertFalse(self.store.status('project01')['portal_ready'])

    def test_release_requires_verifier(self):
        f=Orchestrator(self.store,self.grant,{s:self.adapter() for s in STAGES})
        for _ in STAGES: f.tick('project01')
        self.assertEqual(f.tick('project01')['state'],'release_verifier_missing')

    def test_release_blocked_for_missing_products(self):
        e=self.evidence(); e['products']=[]
        f=Orchestrator(self.store,self.grant,{s:self.adapter() for s in STAGES},verifier=lambda b:e)
        for _ in STAGES: f.tick('project01')
        self.assertEqual(f.tick('project01')['state'],'quality_hold')

    def test_final_report_requires_fresh_evidence(self):
        f=Orchestrator(self.store,self.grant,{s:self.adapter() for s in STAGES},verifier=lambda b:self.evidence())
        for _ in STAGES: f.tick('project01')
        self.assertTrue(f.report('project01')['portal_ready'])
        f.verifier=None
        self.assertFalse(f.report('project01')['portal_ready'])

    def test_malformed_port_not_accepted(self):
        e=self.evidence(); e['portal_url']='https://example.com:bad/'
        self.assertFalse(acceptance(self.brief,e,now=self.now)['portal_ready'])

    def test_acceptance_pass(self): self.assertTrue(acceptance(self.brief,self.evidence(),now=self.now)['portal_ready'])

    def test_acceptance_duplicate_not_counted(self):
        e=self.evidence();e['products'][49]=e['products'][0]
        r=acceptance(self.brief,e,now=self.now)
        self.assertEqual(r['verified_counts']['products'],49)
        self.assertFalse(r['portal_ready'])

    def test_acceptance_no_paypal_no_release(self):
        e=self.evidence();e['checks']['paypal_webhook']=False
        self.assertFalse(acceptance(self.brief,e,now=self.now)['portal_ready'])

    def test_acceptance_draft_not_counted(self):
        e=self.evidence();e['articles'][0]['status']='draft'
        self.assertFalse(acceptance(self.brief,e,now=self.now)['portal_ready'])

    def test_acceptance_fake_delivery(self):
        e=self.evidence();e['products'][0]['delivery_verified']='true'
        self.assertFalse(acceptance(self.brief,e,now=self.now)['portal_ready'])

    def test_acceptance_stale(self):
        e=self.evidence();e['observed_at']-=901
        with self.assertRaises(FactoryError): acceptance(self.brief,e,now=self.now)

    def test_acceptance_wrong_source(self):
        e=self.evidence();e['authenticated']=False
        with self.assertRaises(FactoryError): acceptance(self.brief,e,now=self.now)

    def test_json_schema_closed(self):
        s={'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}
        validate({'ok':True},s)
        with self.assertRaises(FactoryError): validate({'ok':True,'shell':'rm'},s)
        with self.assertRaises(FactoryError): validate({'ok':'true'},s)

    def test_json_bool_not_integer(self):
        with self.assertRaises(FactoryError): validate(True,{'type':'integer'})

    def test_model_requires_pins(self):
        with self.assertRaises(FactoryError): LocalSLM(Path('/missing'),'',Path('/missing'),'')

    def test_model_not_started_is_not_verified(self):
        m=LocalSLM(Path('/missing'),'a'*64,Path('/missing'),'b'*64)
        self.assertFalse(m.ready())
        with self.assertRaises(FactoryError): m.generate('Zwróć JSON',{}, {'type':'object','properties':{},'additionalProperties':False})

    def test_preflight_memory(self):
        model=self.root/'model.gguf';model.write_bytes(b'GGUF'+b'x'*100)
        binary=self.root/'llama-server';binary.write_text('test executable; never run');binary.chmod(0o700)
        m=LocalSLM(model,hashlib.sha256(model.read_bytes()).hexdigest(),binary,hashlib.sha256(binary.read_bytes()).hexdigest())
        with patch('msw_combo_slm.model.available_memory',return_value=128*1024**2):
            with self.assertRaisesRegex(FactoryError,'insufficient_measured_memory'):m.preflight()

    def test_preflight_checksum(self):
        m=LocalSLM(Path('/missing'),'a'*64,Path('/missing'),'b'*64)
        with self.assertRaisesRegex(FactoryError,'integrity'):m.preflight()

if __name__=='__main__': unittest.main()
