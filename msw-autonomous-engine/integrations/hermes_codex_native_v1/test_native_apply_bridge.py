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
            saved['ack']=dict(**{field:job[field] for field in
                ('root_id','task_id','request_id','work_key','parent_id')},
                payload_sha256=saved['digest'])
            return saved['ack']
        def readback(job):
            return dict(**{field:job[field] for field in
                ('root_id','task_id','request_id','work_key','parent_id')},
                ack=saved['ack'],payload_sha256=saved['digest'],effect_verified=True)
        self.assertEqual(apply_verified(job,receipt,apply_once,readback)['status'],'APPLIED_VERIFIED')

    def test_persisted_ack_readback_requires_exact_json_types(self):
        job, receipt = fixture()
        fields = ('root_id', 'task_id', 'request_id', 'work_key', 'parent_id')
        saved = {}
        def apply_once(j, payload):
            saved['digest'] = hashlib.sha256(canonical(payload)).hexdigest()
            saved['ack'] = dict(**{key:j[key] for key in fields},
                                payload_sha256=saved['digest'],
                                status='verified', success=True, attempts=1)
            return saved['ack']
        def readback(j, corrupted=None):
            observed_ack = dict(saved['ack'])
            if corrupted == 'bool_to_int':
                observed_ack['success'] = 1  # True == 1 in Python
            elif corrupted == 'int_to_bool':
                observed_ack['attempts'] = True  # 1 == True in Python
            return dict(**{key:j[key] for key in fields},
                        payload_sha256=saved['digest'],
                        ack=observed_ack, effect_verified=True)
        # Exact native readback still succeeds.
        self.assertEqual(apply_verified(job, receipt, apply_once,
                                      lambda j:readback(j))['status'],
                         'APPLIED_VERIFIED')
        # Both mismatches previously passed with regular Python dict equality.
        for changed in ('bool_to_int', 'int_to_bool'):
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(Hold, 'NATIVE_EFFECT_NOT_VERIFIED'):
                    apply_verified(job, receipt, apply_once,
                                   lambda j, c=changed:readback(j,c))

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

    def test_tools_zero_must_be_exact_integer_before_native_effect(self):
        job,receipt=fixture()
        calls=[]
        def must_not_call(*args):
            calls.append(1)
            raise AssertionError('invalid tools type must not reach native effect')
        # In Python False == 0 and 0.0 == 0, but they are not a typed
        # no-tools receipt. Rehash to rule out simple integrity rejection.
        for tools in (False, 0.0, '0', None, [], {}):
            mutated=dict(receipt,tools=tools)
            mutated.pop('receipt_sha256')
            mutated['receipt_sha256']=hashlib.sha256(canonical(mutated)).hexdigest()
            with self.subTest(tools=repr(tools)):
                with self.assertRaisesRegex(Hold,'CODEX_RECEIPT_UNSAFE'):
                    apply_verified(job,mutated,must_not_call,must_not_call)
        self.assertEqual(calls,[])

    def test_native_ack_requires_complete_original_identity(self):
        job,receipt=fixture()
        required=('root_id','task_id','request_id','work_key','parent_id')
        readback_calls=[]
        def never_readback(*args):
            readback_calls.append(1)
            raise AssertionError('bad native ack must fail before readback')
        for field in required:
            for missing in (False,True):
                def mismatched_apply(j,p, field=field, missing=missing):
                    ack={key:j[key] for key in required}
                    if missing:
                        ack.pop(field)
                    else:
                        ack[field]='foreign'
                    ack['payload_sha256']=hashlib.sha256(canonical(p)).hexdigest()
                    return ack
                with self.subTest(field=field,missing=missing):
                    with self.assertRaisesRegex(Hold,'NATIVE_ACK_MISMATCH'):
                        apply_verified(job,receipt,mismatched_apply,never_readback)
        self.assertEqual(readback_calls,[])

    def test_native_effect_readback_requires_complete_original_identity(self):
        job,receipt=fixture()
        required=('root_id','task_id','request_id','work_key','parent_id')
        def matching_apply(j,p):
            return dict(**{key:j[key] for key in required},
                        payload_sha256=hashlib.sha256(canonical(p)).hexdigest())
        for field in required:
            for missing in (False,True):
                def mismatched_readback(j, field=field, missing=missing):
                    ack=matching_apply(j,{'identity':{key:j[key] for key in required},
                        'codex_receipt_sha256':receipt['receipt_sha256'],
                        'result':receipt['text']})
                    persisted=dict(**{key:j[key] for key in required},
                                   ack=ack,payload_sha256=ack['payload_sha256'],
                                   effect_verified=True)
                    if missing:
                        persisted.pop(field)
                    else:
                        persisted[field]='foreign'
                    return persisted
                with self.subTest(field=field,missing=missing):
                    with self.assertRaisesRegex(Hold,'NATIVE_EFFECT_NOT_VERIFIED'):
                        apply_verified(job,receipt,matching_apply,mismatched_readback)

    def test_noncanonical_codex_receipt_holds_before_native_effect(self):
        job, receipt = fixture()
        calls = []
        def must_not_apply(*args):
            calls.append('unexpected_native_side_effect')
            raise AssertionError('invalid receipt passed admission')
        cases = (
            dict(receipt, text='\ud800'),  # Lone UTF-16 surrogate, not UTF-8
            dict(receipt, extra={1, 2}),  # Not JSON serializable
        )
        for malformed in cases:
            with self.subTest(kind=type(malformed.get('extra')).__name__ if 'extra' in malformed else 'surrogate'):
                with self.assertRaisesRegex(Hold, 'CODEX_RECEIPT_NONCANONICAL'):
                    apply_verified(job, malformed, must_not_apply, must_not_apply)
        self.assertEqual(calls, [])

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

    def test_explicit_negative_ack_cannot_be_treated_as_native_success(self):
        job, receipt = fixture()
        fields = ('root_id', 'task_id', 'request_id', 'work_key', 'parent_id')
        readback_calls = []
        def unexpected_readback(*_):
            readback_calls.append(1)
            raise AssertionError('negative native ACK must block readback')
        for negative in ({'success': False}, {'status': 'FAILED'},
                         {'outcome': 'blocked'}, {'error': 'native rejected'}):
            def negative_apply(j, payload, negative=negative):
                return dict(**{key:j[key] for key in fields},
                            payload_sha256=hashlib.sha256(canonical(payload)).hexdigest(),
                            **negative)
            with self.subTest(negative=negative):
                with self.assertRaisesRegex(Hold, 'NATIVE_ACK_NEGATIVE_OUTCOME'):
                    apply_verified(job, receipt, negative_apply, unexpected_readback)
        self.assertEqual(readback_calls, [])

    def test_nonterminal_native_ack_holds_even_with_matching_hashes(self):
        job, receipt = fixture()
        fields = ('root_id', 'task_id', 'request_id', 'work_key', 'parent_id')
        calls = []
        def readback_never(*_):
            calls.append('readback')
            raise AssertionError('nonterminal ACK must not be accepted')
        for signal in ({'status': 'processing'}, {'state': 'queued'},
                       {'outcome': 'accepted'}, {'status': 'undocumented_state'},
                       {'status': None}, {'success': 0}, {'success': 1}):
            def apply_once(j, payload, signal=signal):
                return dict(**{key:j[key] for key in fields},
                            payload_sha256=hashlib.sha256(canonical(payload)).hexdigest(),
                            **signal)
            with self.subTest(signal=signal):
                with self.assertRaisesRegex(Hold, 'NATIVE_ACK_NEGATIVE_OUTCOME'):
                    apply_verified(job, receipt, apply_once, readback_never)
        self.assertEqual(calls, [])

    def test_nonterminal_persisted_effect_holds_even_with_effect_flag(self):
        job, receipt = fixture()
        fields = ('root_id', 'task_id', 'request_id', 'work_key', 'parent_id')
        saved = {}
        def apply_once(j, payload):
            saved['digest'] = hashlib.sha256(canonical(payload)).hexdigest()
            saved['ack'] = dict(**{key:j[key] for key in fields},
                                payload_sha256=saved['digest'], status='verified')
            return saved['ack']
        for signal in ({'state': 'in_progress'}, {'outcome': 'reported'},
                       {'status': 'accepted'}, {'status': 'undocumented_state'},
                       {'success': None}):
            def readback(j, signal=signal):
                return dict(**{key:j[key] for key in fields},
                            payload_sha256=saved['digest'], ack=saved['ack'],
                            effect_verified=True, **signal)
            with self.subTest(signal=signal):
                with self.assertRaisesRegex(Hold, 'NATIVE_EFFECT_NEGATIVE_OUTCOME'):
                    apply_verified(job, receipt, apply_once, readback)


    def test_transport_ack_and_generic_completion_are_not_native_effect(self):
        # ACK may genuinely confirm transport; persisted effect cannot reuse
        # "acknowledged", "completed" or "verified" as native-apply evidence.
        job, receipt = fixture()
        fields = ('root_id', 'task_id', 'request_id', 'work_key', 'parent_id')
        saved = {}
        def apply_once(j, payload):
            saved['digest'] = hashlib.sha256(canonical(payload)).hexdigest()
            saved['ack'] = dict(**{key:j[key] for key in fields},
                                payload_sha256=saved['digest'],
                                status='acknowledged')
            return saved['ack']
        def readback(j, state):
            return dict(**{key:j[key] for key in fields},
                        payload_sha256=saved['digest'],
                        ack=saved['ack'], effect_verified=True, state=state)
        for state in ('acknowledged', 'completed', 'verified',
                      'succeeded', 'success', 'reported', 'processing'):
            with self.subTest(persisted_state=state):
                with self.assertRaisesRegex(Hold, 'NATIVE_EFFECT_NEGATIVE_OUTCOME'):
                    apply_verified(job, receipt, apply_once,
                                   lambda j, state=state: readback(j, state))
        # An independently observed native effect is different from transport.
        verified = apply_verified(job, receipt, apply_once,
                                  lambda j: readback(j, 'native_observed'))
        self.assertEqual(verified['status'], 'APPLIED_VERIFIED')

    def test_explicit_negative_effect_cannot_override_effect_verified(self):
        job, receipt = fixture()
        fields = ('root_id', 'task_id', 'request_id', 'work_key', 'parent_id')
        saved = {}
        def apply_once(j, payload):
            saved['digest'] = hashlib.sha256(canonical(payload)).hexdigest()
            saved['ack'] = dict(**{key:j[key] for key in fields},
                                payload_sha256=saved['digest'])
            return saved['ack']
        for negative in ({'success': False}, {'status': 'rejected'},
                         {'state': 'unknown'}, {'error': 'effect denied'}):
            def negative_readback(j, negative=negative):
                return dict(**{key:j[key] for key in fields},
                            ack=saved['ack'], payload_sha256=saved['digest'],
                            effect_verified=True, **negative)
            with self.subTest(negative=negative):
                with self.assertRaisesRegex(Hold, 'NATIVE_EFFECT_NEGATIVE_OUTCOME'):
                    apply_verified(job, receipt, apply_once, negative_readback)

if __name__=='__main__':unittest.main()
