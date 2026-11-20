"""Offline regression tests for native_apply_bridge; no production calls."""
import hashlib
import unittest
from native_apply_bridge import apply_verified,canonical,Hold

def fixture():
    job=dict(root_id='r',task_id='t',request_id='q',work_key='w',parent_id='p',
             lease_verified=True,state='leased',native_apply_contract_verified=True)
    receipt=dict(status='VERIFIED',independent_readback=True,tools=0,text='OK',
                 response_sha256=hashlib.sha256(b'OK').hexdigest(),
                 **{field:job[field] for field in ('root_id','task_id','request_id','work_key','parent_id')})
    receipt['receipt_sha256']=hashlib.sha256(canonical(receipt)).hexdigest()
    return job,receipt

class NativeApplyTests(unittest.TestCase):
    def test_verified_apply_and_readback(self):
        job,receipt=fixture()
        saved={}
        def apply_once(job,payload):
            saved['digest']=hashlib.sha256(canonical(payload)).hexdigest()
            saved['ack']=dict(task_id=job['task_id'],work_key=job['work_key'],
                              payload_sha256=saved['digest'])
            return saved['ack']
        def readback(job):
            return dict(ack=saved['ack'],payload_sha256=saved['digest'],effect_verified=True)
        self.assertEqual(apply_verified(job,receipt,apply_once,readback)['status'],'APPLIED_VERIFIED')

    def test_fail_closed_before_apply(self):
        job,receipt=fixture()
        def must_not_call(*args):raise AssertionError('unexpected side effect')
        for change in (dict(lease_verified=False),dict(native_apply_contract_verified=False),
                       dict(state='held')):
            with self.subTest(change=change),self.assertRaises(Hold):
                apply_verified(dict(job,**change),receipt,must_not_call,must_not_call)
        for change in (dict(status='UNKNOWN'),dict(receipt_sha256='bad'),dict(tools=1)):
            with self.subTest(change=change),self.assertRaises(Hold):
                apply_verified(job,dict(receipt,**change),must_not_call,must_not_call)

    def test_cross_root_and_cross_request_receipts_fail_before_apply(self):
        job,receipt=fixture()
        calls=[]
        def must_not_call(*args):
            calls.append(1)
            raise AssertionError('foreign receipt must not reach native apply')
        for field in ('root_id','task_id','request_id','work_key','parent_id'):
            for missing in (False,True):
                mutated=dict(receipt)
                if missing:
                    mutated.pop(field)
                else:
                    mutated[field]='foreign-value'
                # An attacker can rehash their own document. The receipt must
                # still be rejected because its source identity is different.
                mutated.pop('receipt_sha256',None)
                mutated['receipt_sha256']=hashlib.sha256(canonical(mutated)).hexdigest()
                with self.subTest(field=field,missing=missing):
                    with self.assertRaisesRegex(Hold,'CODEX_RECEIPT_'+field.upper()+'_MISMATCH'):
                        apply_verified(job,mutated,must_not_call,must_not_call)
        self.assertEqual(calls,[])

    def test_ack_mismatch(self):
        job,receipt=fixture()
        with self.assertRaisesRegex(Hold,'NATIVE_ACK_MISMATCH'):
            apply_verified(job,receipt,lambda j,p:dict(task_id='wrong'),lambda j:None)

    def test_unknown_apply_does_not_retry(self):
        job,receipt=fixture()
        calls=[]
        def broken(j,p):
            calls.append(1)
            raise RuntimeError('timeout after uncertain write')
        with self.assertRaisesRegex(Hold,'NATIVE_APPLY_UNKNOWN_OUTCOME'):
            apply_verified(job,receipt,broken,lambda j:None)
        self.assertEqual(len(calls),1)

if __name__=='__main__':unittest.main()
