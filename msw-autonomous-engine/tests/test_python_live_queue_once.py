import hashlib,json,os,copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from msw_engine import python_live_queue_once as p

PAYLOAD = json.dumps({'ideas':[{'id':'fixture-a','title':'Synthetic A','demand':80,'competition':20,'margin':70,'delivery_effort':30,'sources':['https://example.org/']}],'classification':'synthetic_public_test','proof_id':'msw-python-link-proof-20261114-v1-rank'},ensure_ascii=False,separators=(',',':'))

class Client:
    def __init__(self):
        self.calls=[]
        self.claim={'state':'claimed','job':{'id':p.JOB,'kind':p.KIND,'payload':PAYLOAD,'payload_sha256':p.PAYLOAD_SHA},'lease_token':'f'*64}
    def send(self,action,data):
        self.calls.append(action)
        if action=='claim':return self.claim
        return {'id':p.JOB,'state':'completed','payload_sha256':p.PAYLOAD_SHA,'result_sha256':'a'*64}
class Worker:
    def __init__(self):self.execs=0;self.acks=0;self.valid=True
    def execute(self,job,lease):
        self.execs+=1
        return {'job_id':p.JOB,'payload_sha256':p.PAYLOAD_SHA,'result_sha256':'a'*64}
    def acknowledge(self,receipt):
        self.acks+=1
        return {'job_id':p.JOB,'state':'completed','server_ack_verified':self.valid}

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'private';self.client=Client();self.worker=Worker()
    def run_once(self):return p.run_exact_once(self.client,self.worker,self.root)
    def test_pinned_payload_bytes_match_actual_wordpress_job(self):
        self.assertEqual(hashlib.sha256(PAYLOAD.encode()).hexdigest(),p.PAYLOAD_SHA)
    def test_complete_uses_real_worker_path_and_does_not_claim_central_identity(self):
        r=self.run_once();self.assertTrue(r['server_ack_verified']);self.assertFalse(r['central_runtime_executed'])
        self.assertEqual(self.worker.execs,1);self.assertEqual(self.worker.acks,1)
    def test_restart_reads_result_without_new_claim_or_execute(self):
        self.run_once();r=self.run_once();self.assertTrue(r['reused'])
        self.assertEqual(self.client.calls,['claim','status']);self.assertEqual(self.worker.execs,1)
    def test_wrong_id_never_executes(self):
        self.client.claim['job']['id']='unknown'
        with self.assertRaisesRegex(p.ProofError,'claimed_job_mismatch'):self.run_once()
        self.assertEqual(self.worker.execs,0)
    def test_wrong_kind_never_executes(self):
        self.client.claim['job']['kind']='llm.generate'
        with self.assertRaises(p.ProofError):self.run_once()
        self.assertEqual(self.worker.execs,0)
    def test_corrupt_input_never_executes(self):
        self.client.claim['job']['payload']+=' '
        with self.assertRaises(p.ProofError):self.run_once()
        self.assertEqual(self.worker.execs,0)
    def test_empty_claim_not_automatically_retried(self):
        self.client.claim={'state':'idle'}
        with self.assertRaises(p.ProofError):self.run_once()
        self.assertEqual(self.run_once()['state'],'prior_admission_requires_review')
        self.assertEqual(self.client.calls,['claim'])
    def test_unknown_transport_outcome_never_claims_twice(self):
        with patch.object(self.client,'send',side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):self.run_once()
        self.assertEqual(self.run_once()['state'],'prior_admission_requires_review');self.assertEqual(self.worker.execs,0)
    def test_no_fake_ack_when_readback_unverified(self):
        self.worker.valid=False
        with self.assertRaisesRegex(p.ProofError,'server_ack_unverified'):self.run_once()
        self.assertFalse(self.run_once()['server_ack_verified']);self.assertEqual(self.worker.execs,1)
    def test_invalid_lease_denied(self):
        self.client.claim['lease_token']='short'
        with self.assertRaises(p.ProofError):self.run_once()
        self.assertEqual(self.worker.execs,0)
    def test_symlink_denied(self):
        self.root.symlink_to(Path(self.tmp.name),target_is_directory=True)
        with self.assertRaisesRegex(p.ProofError,'symlink'):self.run_once()
    def test_disabled_by_default(self):
        with patch.dict(os.environ,{},clear=True):self.assertEqual(p.run_live_once()['state'],'disabled')
    def test_wrong_node_denied(self):
        with patch.dict(os.environ,{'MSW_PYTHON_LIVE_QUEUE_PROOF':'1','RENDER_SERVICE_ID':p.SERVICE,'MSW_NODE':'central'},clear=True):
            self.assertEqual(p.run_live_once()['state'],'existing_auxiliary_identity_missing')
    def test_missing_transport_denied(self):
        with patch.dict(os.environ,{'MSW_PYTHON_LIVE_QUEUE_PROOF':'1','RENDER_SERVICE_ID':p.SERVICE,'MSW_NODE':p.NODE},clear=True):
            self.assertEqual(p.run_live_once()['state'],'existing_signed_transport_missing')
if __name__=='__main__':unittest.main()
