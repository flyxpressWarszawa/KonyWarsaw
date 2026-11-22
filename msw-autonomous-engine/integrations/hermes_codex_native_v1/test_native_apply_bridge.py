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
    def test_python_only_context_cannot_change_type_after_json_roundtrip(self):
        # The old canonical encoder converted numeric keys to text and tuples
        # to arrays while reporting the same admitted hash as the converted
        # native callback input. No callback may run after such conversion.
        job, receipt = fixture()
        calls = []
        def forbidden(*_):
            calls.append('native_called')
            raise AssertionError('non-JSON job reached native callback')
        for context in ({'grant': {1: 'active'}},
                        {'grant': ('fence', 'lease')}):
            with self.subTest(context=context):
                with self.assertRaisesRegex(Hold, 'JOB_OR_PAYLOAD_NONCANONICAL'):
                    apply_verified(dict(job, native_context=context),
                                   receipt, forbidden, forbidden)
        self.assertEqual(calls, [])

    def test_verified_apply_and_readback(self):
        job,receipt=fixture()
        saved={}
        def apply_once(job,payload):
            saved['digest']=hashlib.sha256(canonical(payload)).hexdigest()
            saved['ack']=dict(**{field:job[field] for field in
                ('root_id','task_id','request_id','work_key','parent_id')},
                payload_sha256=saved['digest'], status='verified')
            return saved['ack']
        def readback(job):
            return dict(**{field:job[field] for field in
                ('root_id','task_id','request_id','work_key','parent_id')},
                ack=saved['ack'],payload_sha256=saved['digest'],effect_verified=True, state='applied')
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
                        ack=observed_ack, effect_verified=True, state='native_observed')
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
                        payload_sha256=hashlib.sha256(canonical(p)).hexdigest(), status='verified')
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

    def test_missing_native_effect_state_cannot_promote_storage_receipt(self):
        job, receipt = fixture()
        fields = ('root_id', 'task_id', 'request_id', 'work_key', 'parent_id')
        saved = {}
        def apply_once(j, payload):
            saved['digest'] = hashlib.sha256(canonical(payload)).hexdigest()
            saved['ack'] = dict(**{key:j[key] for key in fields},
                                payload_sha256=saved['digest'], status='acknowledged')
            return saved['ack']
        for incomplete in (
            {},
            {'success': True},
            {'effect_verified': True},
        ):
            def readback(j, incomplete=incomplete):
                return dict(**{key:j[key] for key in fields},
                            ack=saved['ack'], payload_sha256=saved['digest'],
                            **incomplete)
            with self.subTest(incomplete=incomplete):
                with self.assertRaisesRegex(Hold, 'NATIVE_EFFECT_NOT_VERIFIED|NATIVE_EFFECT_NEGATIVE_OUTCOME'):
                    apply_verified(job, receipt, apply_once, readback)

    def test_explicit_native_effect_state_still_requires_effect_flag(self):
        job, receipt = fixture()
        fields = ('root_id', 'task_id', 'request_id', 'work_key', 'parent_id')
        saved = {}
        def apply_once(j, payload):
            saved['digest'] = hashlib.sha256(canonical(payload)).hexdigest()
            saved['ack'] = dict(**{key:j[key] for key in fields},
                                payload_sha256=saved['digest'], status='acknowledged')
            return saved['ack']
        def readback(j):
            return dict(**{key:j[key] for key in fields},
                        ack=saved['ack'], payload_sha256=saved['digest'],
                        state='applied', effect_verified=False)
        with self.assertRaisesRegex(Hold, 'NATIVE_EFFECT_NOT_VERIFIED'):
            apply_verified(job, receipt, apply_once, readback)

    def test_native_callbacks_cannot_rebind_original_identity(self):
        # Native callbacks must not mutate the original admitted job so a
        # foreign ACK/effect can be checked against a rewritten root_id.
        fields = ('root_id', 'task_id', 'request_id', 'work_key', 'parent_id')
        for phase in ('apply', 'readback'):
            job, receipt = fixture()
            original = {key: job[key] for key in fields}
            saved = {}
            def apply_once(callback_job, payload):
                saved['digest'] = hashlib.sha256(canonical(payload)).hexdigest()
                if phase == 'apply':
                    callback_job['root_id'] = 'foreign-root'
                saved['ack'] = dict(**{key: callback_job[key] for key in fields},
                                    payload_sha256=saved['digest'],
                                    status='verified')
                return saved['ack']
            def readback(callback_job):
                if phase == 'readback':
                    callback_job['root_id'] = 'foreign-root'
                return dict(**{key: callback_job[key] for key in fields},
                            ack=saved['ack'], payload_sha256=saved['digest'],
                            effect_verified=True, state='native_observed')
            with self.subTest(phase=phase):
                with self.assertRaisesRegex(
                        Hold, 'NATIVE_APPLY_JOB_CONTEXT_MUTATED|NATIVE_READBACK_JOB_CONTEXT_MUTATED'):
                    apply_verified(job, receipt, apply_once, readback)
                self.assertEqual({key:job[key] for key in fields}, original)

    def test_callback_payload_mutation_cannot_look_like_native_success(self):
        # A callback may return matching *original* digests after it has
        # changed the payload it was handed. Without a post-call byte check
        # the bridge could report APPLIED_VERIFIED for a different effect.
        fields = ('root_id', 'task_id', 'request_id', 'work_key', 'parent_id')
        for change in ('identity', 'result', 'noncanonical'):
            job, receipt = fixture()
            calls = []
            def mutating_apply(callback_job, payload):
                calls.append('apply')
                original_digest = hashlib.sha256(canonical(payload)).hexdigest()
                if change == 'identity':
                    payload['identity']['root_id'] = 'foreign-root'
                elif change == 'result':
                    payload['result'] = 'different native instruction'
                else:
                    payload['identity']['extra'] = {1, 2}
                return dict(**{k:callback_job[k] for k in fields},
                            payload_sha256=original_digest, status='verified')
            def readback_never(callback_job):
                calls.append('readback')
                raise AssertionError('mutated apply input was accepted')
            with self.subTest(change=change):
                with self.assertRaisesRegex(Hold, 'NATIVE_APPLY_INPUT_MUTATED'):
                    apply_verified(job, receipt, mutating_apply, readback_never)
                self.assertEqual(calls, ['apply'])
                self.assertEqual(job['root_id'], 'r')

    def test_readback_cannot_rewrite_original_native_ack(self):
        # The readback callback may retain a reference to apply_once's ACK.
        # If it mutates that object and returns the same object as its own
        # "persisted" ACK, dict equality alone previously accepted the pair.
        fields = ('root_id', 'task_id', 'request_id', 'work_key', 'parent_id')
        for changed in ('status', 'nested', 'noncanonical'):
            job, receipt = fixture()
            saved = {}
            calls = []
            def apply_once(callback_job, payload):
                calls.append('apply')
                saved['digest'] = hashlib.sha256(canonical(payload)).hexdigest()
                saved['ack'] = dict(**{k:callback_job[k] for k in fields},
                                    payload_sha256=saved['digest'],
                                    status='verified', evidence={'serial': 'original'})
                return saved['ack']
            def readback(callback_job):
                calls.append('readback')
                if changed == 'status':
                    saved['ack']['status'] = 'failed'
                elif changed == 'nested':
                    saved['ack']['evidence']['serial'] = 'rewritten'
                else:
                    saved['ack']['evidence'] = {1, 2}
                return dict(**{k:callback_job[k] for k in fields},
                            payload_sha256=saved['digest'], ack=saved['ack'],
                            effect_verified=True, state='native_observed')
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(Hold, 'NATIVE_ACK_MUTATED_DURING_READBACK'):
                    apply_verified(job, receipt, apply_once, readback)
                self.assertEqual(calls, ['apply', 'readback'])
                self.assertEqual(job['root_id'], 'r')

    def test_readback_cannot_mutate_admitted_apply_payload(self):
        # A native apply callback can retain a reference to its input payload.
        # A later readback must not mutate that shared object after the
        # bridge's initial post-apply integrity check and still report success.
        fields = ('root_id', 'task_id', 'request_id', 'work_key', 'parent_id')
        for change in ('result', 'nested_identity', 'noncanonical'):
            job, receipt = fixture()
            saved = {}
            calls = []
            def apply_once(j, payload):
                calls.append('apply')
                saved['payload'] = payload
                digest = hashlib.sha256(canonical(payload)).hexdigest()
                saved['digest'] = digest
                saved['ack'] = dict(**{k:j[k] for k in fields},
                                    payload_sha256=digest, status='verified')
                return saved['ack']
            def readback(j):
                calls.append('readback')
                if change == 'result':
                    saved['payload']['result'] = 'changed after apply'
                elif change == 'nested_identity':
                    saved['payload']['identity']['root_id'] = 'foreign'
                else:
                    saved['payload']['identity']['extra'] = {1, 2}
                return dict(**{k:j[k] for k in fields}, ack=saved['ack'],
                            payload_sha256=saved['digest'],
                            effect_verified=True, state='native_observed')
            with self.subTest(change=change):
                with self.assertRaisesRegex(
                        Hold, 'NATIVE_APPLY_INPUT_MUTATED_DURING_READBACK'):
                    apply_verified(job, receipt, apply_once, readback)
                self.assertEqual(calls, ['apply', 'readback'])
                self.assertEqual(job['root_id'], 'r')

    def test_admitted_codex_receipt_cannot_change_after_native_callback(self):
        # A native callback may retain the original caller receipt through a
        # closure. Without a post-call check, the bridge can return VERIFIED
        # for a receipt whose admitted content was altered after hashing.
        fields = ('root_id', 'task_id', 'request_id', 'work_key', 'parent_id')
        for phase, change in (('apply', 'nested'), ('readback', 'text'),
                              ('readback', 'noncanonical')):
            job, receipt = fixture()
            receipt['evidence'] = {'source': 'admitted'}
            body = {k:v for k,v in receipt.items() if k != 'receipt_sha256'}
            receipt['receipt_sha256'] = hashlib.sha256(canonical(body)).hexdigest()
            calls = []
            saved = {}
            def mutate_source_receipt():
                if change == 'nested':
                    receipt['evidence']['source'] = 'rewritten'
                elif change == 'text':
                    receipt['text'] = 'different native result'
                else:
                    receipt['evidence']['source'] = {1, 2}
            def native_apply(j, payload):
                calls.append('apply')
                saved['digest'] = hashlib.sha256(canonical(payload)).hexdigest()
                saved['ack'] = dict(**{k:j[k] for k in fields},
                                    payload_sha256=saved['digest'],
                                    status='verified')
                if phase == 'apply':
                    mutate_source_receipt()
                return saved['ack']
            def native_readback(j):
                calls.append('readback')
                if phase == 'readback':
                    mutate_source_receipt()
                return dict(**{k:j[k] for k in fields},
                            payload_sha256=saved['digest'], ack=saved['ack'],
                            effect_verified=True, state='native_observed')
            reason = ('CODEX_RECEIPT_MUTATED_DURING_NATIVE_APPLY'
                      if phase == 'apply'
                      else 'CODEX_RECEIPT_MUTATED_DURING_NATIVE_READBACK')
            with self.subTest(phase=phase, change=change):
                with self.assertRaisesRegex(Hold, reason):
                    apply_verified(job, receipt, native_apply, native_readback)
                self.assertEqual(calls, ['apply'] if phase == 'apply'
                                 else ['apply', 'readback'])

    def test_native_callbacks_cannot_mutate_admitted_job_context(self):
        # A lease flag, native grant or nested admission proof changed by a
        # callback is not the original admitted work, even when all five IDs
        # and the ACK digest still match. HOLD after effect, never retry.
        fields = ('root_id', 'task_id', 'request_id', 'work_key', 'parent_id')
        for phase in ('apply', 'readback'):
            for changed in ('state', 'nested_grant', 'noncanonical'):
                job, receipt = fixture()
                job['grant_context'] = {'fence_epoch': 7}
                calls = []
                saved = {}
                def mutate(callback_job):
                    if changed == 'state':
                        callback_job['state'] = 'held'
                    elif changed == 'nested_grant':
                        callback_job['grant_context']['fence_epoch'] = 8
                    else:
                        callback_job['grant_context']['invalid'] = {1, 2}
                def native_apply(callback_job, payload):
                    calls.append('apply')
                    saved['digest'] = hashlib.sha256(canonical(payload)).hexdigest()
                    saved['ack'] = dict(**{k: callback_job[k] for k in fields},
                                        payload_sha256=saved['digest'],
                                        status='verified')
                    if phase == 'apply':
                        mutate(callback_job)
                    return saved['ack']
                def native_readback(callback_job):
                    calls.append('readback')
                    if phase == 'readback':
                        mutate(callback_job)
                    return dict(**{k: callback_job[k] for k in fields},
                                ack=saved['ack'], payload_sha256=saved['digest'],
                                effect_verified=True, state='native_observed')
                reason = ('NATIVE_APPLY_JOB_CONTEXT_MUTATED'
                          if phase == 'apply'
                          else 'NATIVE_READBACK_JOB_CONTEXT_MUTATED')
                with self.subTest(phase=phase, changed=changed):
                    with self.assertRaisesRegex(Hold, reason):
                        apply_verified(job, receipt, native_apply, native_readback)
                    self.assertEqual(calls, ['apply'] if phase == 'apply'
                                     else ['apply', 'readback'])
                    self.assertEqual(job['state'], 'leased')
                    self.assertEqual(job['grant_context'], {'fence_epoch': 7})

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

    def test_native_callback_cancellation_is_unknown_and_not_replayed(self):
        # Python asyncio.CancelledError derives from BaseException, not
        # Exception. A callback can be cancelled after the native write; its
        # outcome must be UNKNOWN, never a signal to retry the same effect.
        from asyncio import CancelledError
        job, receipt = fixture()
        calls = []
        def cancelled_apply(j, payload):
            calls.append('apply')
            raise CancelledError()
        def never_readback(j):
            calls.append('readback')
            raise AssertionError('unexpected readback')
        with self.assertRaisesRegex(Hold, 'NATIVE_APPLY_UNKNOWN_OUTCOME'):
            apply_verified(job, receipt, cancelled_apply, never_readback)
        self.assertEqual(calls, ['apply'])
        saved = {}
        def accepted_apply(j, payload):
            calls.append('apply')
            saved['digest'] = hashlib.sha256(canonical(payload)).hexdigest()
            return dict(**{key:j[key] for key in
                ('root_id', 'task_id', 'request_id', 'work_key', 'parent_id')},
                payload_sha256=saved['digest'], status='verified')
        def cancelled_readback(j):
            calls.append('readback')
            raise CancelledError()
        with self.assertRaisesRegex(Hold, 'NATIVE_READBACK_UNKNOWN'):
            apply_verified(job, receipt, accepted_apply, cancelled_readback)
        self.assertEqual(calls, ['apply', 'apply', 'readback'])

    def test_numeric_error_does_not_masquerade_as_false_native_error(self):
        # A real native ACK/effect may report error=0 or 0.0. Python considers
        # both equal to False, but neither is an authenticated no-error flag.
        job, receipt = fixture()
        fields = ('root_id', 'task_id', 'request_id', 'work_key', 'parent_id')
        for bad_error in (0, 0.0):
            with self.subTest(stage='ack', error=bad_error):
                calls = []
                def apply_once(j, payload):
                    calls.append('apply')
                    return dict(**{k:j[k] for k in fields},
                                payload_sha256=hashlib.sha256(canonical(payload)).hexdigest(),
                                status='verified', error=bad_error)
                def no_readback(j):
                    calls.append('readback')
                    raise AssertionError('negative ACK entered readback')
                with self.assertRaisesRegex(Hold, 'NATIVE_ACK_NEGATIVE_OUTCOME'):
                    apply_verified(job, receipt, apply_once, no_readback)
                self.assertEqual(calls, ['apply'])
            with self.subTest(stage='effect', error=bad_error):
                state = {}
                def accepted(j, payload):
                    state['digest'] = hashlib.sha256(canonical(payload)).hexdigest()
                    state['ack'] = dict(**{k:j[k] for k in fields},
                                        payload_sha256=state['digest'], status='verified')
                    return state['ack']
                def bad_readback(j):
                    return dict(**{k:j[k] for k in fields},
                                ack=state['ack'], payload_sha256=state['digest'],
                                effect_verified=True, state='native_observed',
                                error=bad_error)
                with self.assertRaisesRegex(Hold, 'NATIVE_EFFECT_NEGATIVE_OUTCOME'):
                    apply_verified(job, receipt, accepted, bad_readback)

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

    def test_statusless_native_ack_does_not_promote_hashes_to_native_apply(self):
        # A matching identity and digest alone can be transport storage; only
        # the native owner can issue a terminal status. No readback or retry
        # should be attempted when the returned ACK lacks one.
        job, receipt = fixture()
        fields = ('root_id', 'task_id', 'request_id', 'work_key', 'parent_id')
        readback_calls = []
        def unexpected_readback(*_):
            readback_calls.append('readback')
            raise AssertionError('missing native ACK state reached readback')
        for extra in ({}, {'success': True}, {'error': None}):
            def statusless_apply(j, payload, extra=extra):
                return dict(**{key:j[key] for key in fields},
                            payload_sha256=hashlib.sha256(canonical(payload)).hexdigest(),
                            **extra)
            with self.subTest(extra=extra):
                with self.assertRaisesRegex(Hold, 'NATIVE_ACK_NEGATIVE_OUTCOME'):
                    apply_verified(job, receipt, statusless_apply, unexpected_readback)
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
                                payload_sha256=saved['digest'], status='verified')
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
