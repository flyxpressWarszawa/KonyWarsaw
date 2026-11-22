"""Fail-closed Hermes native apply bridge. No Codex dispatch or queue mutation built in."""
import hashlib
import json
from asyncio import CancelledError

class Hold(RuntimeError): pass

def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()

def _explicit_negative_native_outcome(record, *, persisted_effect=False):
    """Treat explicit native failure/denial as HOLD even when hashes match.

    Absence of a status field is not success evidence: identity, payload and
    independent effect readback remain mandatory below. Never retry on HOLD.
    """
    if not isinstance(record, dict):
        return False
    # Python considers 0 == False and 1 == True, but native success must
    # be an actual Boolean True when that field is present. Unknown is HOLD.
    if 'success' in record and record['success'] is not True:
        return True
    # Equality would treat numeric 0 / 0.0 as False; these are not valid
    # no-error sentinels from the authenticated native receiver. Fail closed.
    if 'error' in record:
        error = record['error']
        if error is not None and error is not False and error != '':
            return True
    # An explicit "queued", "processing", "accepted" or unfamiliar state is
    # not final native application. Do not infer success from matching hashes.
    terminal = {'success', 'succeeded', 'completed', 'verified', 'applied',
                'applied_verified', 'acknowledged', 'native_observed'}
    if persisted_effect:
        # A transport/storage ACK or a generic "completed" is not evidence
        # that Hermes natively applied the result. If the persisted record
        # exposes a state, only an explicit native-apply state can qualify.
        # The receiver must still authenticate provenance independently.
        terminal = {'applied', 'applied_verified', 'native_observed'}
    # BOTH the native ACK and the independently read persisted effect must
    # carry an explicit terminal state. Identifiers and digests alone prove
    # integrity, not application; success=True alone is not an ACK status.
    # A status-less ACK previously passed through to a matching readback.
    # Refuse without retrying the possibly already applied operation.
    if not any(key in record for key in ('status', 'state', 'outcome')):
        return True
    for key in ('status', 'state', 'outcome'):
        if key in record:
            value = record[key]
            if not isinstance(value, str) or value.strip().lower() not in terminal:
                return True
    return False

def _same_canonical_json(left, right):
    """Compare readback to original ACK without Python bool/int loose equality.

    Native provenance is checked by the authorised receiver; this is only a
    byte/type integrity guard. Unserializable evidence fails closed.
    """
    try:
        return canonical(left) == canonical(right)
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        return False

def apply_verified(job, codex_receipt, apply_once, readback):
    """Require native Hermes ACK and independent persisted-effect readback.

    apply_once must atomically compare-and-set by existing work_key. Never retry
    when native apply outcome is unknown. Caller must own and revalidate lease.
    """
    required=('root_id','task_id','request_id','work_key','parent_id')
    if not isinstance(job,dict) or any(not isinstance(job.get(k),str) or not job[k] for k in required):
        raise Hold('INVALID_JOB_IDENTITY')
    if job.get('lease_verified') is not True or job.get('state') != 'leased':
        raise Hold('LEASE_NOT_VERIFIED')
    if job.get('native_apply_contract_verified') is not True:
        raise Hold('NATIVE_APPLY_CONTRACT_UNVERIFIED')
    if not isinstance(codex_receipt,dict) or codex_receipt.get('status')!='VERIFIED' or codex_receipt.get('independent_readback') is not True:
        raise Hold('CODEX_RECEIPT_UNVERIFIED')
    # bool and float compare equal to integer zero in Python; neither is a
    # valid no-tools attestation. Require the exact integer type.
    if (type(codex_receipt.get('tools')) is not int
            or codex_receipt['tools'] != 0
            or not isinstance(codex_receipt.get('text'),str)):
        raise Hold('CODEX_RECEIPT_UNSAFE')
    body={k:v for k,v in codex_receipt.items() if k!='receipt_sha256'}
    # External evidence may contain an invalid Unicode scalar or non-JSON
    # object. Refuse it as HOLD rather than leaking an uncaught encoder error.
    # All verification remains BEFORE apply_once (no native side effects).
    try:
        receipt_digest=hashlib.sha256(canonical(body)).hexdigest()
        response_digest=hashlib.sha256(codex_receipt['text'].encode('utf-8', errors='strict')).hexdigest()
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        raise Hold('CODEX_RECEIPT_NONCANONICAL') from exc
    if receipt_digest!=codex_receipt.get('receipt_sha256'):
        raise Hold('CODEX_RECEIPT_DIGEST_MISMATCH')
    if response_digest!=codex_receipt.get('response_sha256'):
        raise Hold('CODEX_TEXT_DIGEST_MISMATCH')
    # A valid digest proves only receipt integrity, not ownership of this job.
    # Check the COMPLETE identity before invoking any potentially costly or
    # irreversible native apply; an old/cross-root receipt must fail closed.
    for field in required:
        if codex_receipt.get(field) != job[field]:
            raise Hold('CODEX_RECEIPT_'+field.upper()+'_MISMATCH')
    # Freeze the admitted identity before calling native callbacks. Passing a
    # separate dict prevents an adapter from mutating the caller's job and
    # making a foreign ACK/readback appear to match the changed identity.
    identity={k:job[k] for k in required}
    admitted_job=dict(job)
    payload={'identity':dict(identity),'codex_receipt_sha256':codex_receipt['receipt_sha256'],'result':codex_receipt['text']}
    # Retain the EXACT admitted bytes. A native callback may mutate the dict
    # passed into apply_once; its matching ACK digest is not evidence that
    # the native effect used the original request. After any such call the
    # effect is uncertain, so HOLD without another apply or Codex dispatch.
    # Freeze the complete admitted lease/context, not only the five ID fields.
    # Native callbacks receive detached JSON copies; a changed grant/state or
    # nested evidence is not the same admitted work even with matching IDs.
    try:
        original_job_bytes=canonical(admitted_job)
        original_payload_bytes=canonical(payload)
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        raise Hold('JOB_OR_PAYLOAD_NONCANONICAL') from exc
    payload_digest=hashlib.sha256(original_payload_bytes).hexdigest()
    apply_job=json.loads(original_job_bytes)
    try:
        ack=apply_once(apply_job,payload)
    except (Exception, CancelledError) as exc:
        raise Hold('NATIVE_APPLY_UNKNOWN_OUTCOME') from exc
    try:
        if canonical(apply_job) != original_job_bytes or canonical(job) != original_job_bytes:
            raise Hold('NATIVE_APPLY_JOB_CONTEXT_MUTATED')
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        raise Hold('NATIVE_APPLY_JOB_CONTEXT_MUTATED') from exc
    try:
        if canonical(payload) != original_payload_bytes:
            raise Hold('NATIVE_APPLY_INPUT_MUTATED')
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        raise Hold('NATIVE_APPLY_INPUT_MUTATED') from exc
    # A matching task/work key and payload hash are insufficient: a native
    # ACK must also bind the original root, request, and parent, not merely
    # describe some persisted bytes. The authenticated receiver is responsible
    # for issuing this complete record; this check is not a signature verifier.
    if any(job.get(field) != value for field,value in identity.items()):
        raise Hold('JOB_IDENTITY_MUTATED')
    if (not isinstance(ack,dict)
            or any(ack.get(field) != identity[field] for field in required)
            or ack.get('payload_sha256') != payload_digest):
        raise Hold('NATIVE_ACK_MISMATCH')
    if _explicit_negative_native_outcome(ack):
        raise Hold('NATIVE_ACK_NEGATIVE_OUTCOME')
    # Pin the EXACT ACK bytes before crossing into the readback callback.
    # The callback may hold a reference to the ACK returned by apply_once;
    # comparing persisted['ack'] to that mutable object after readback could
    # falsely validate two jointly modified objects as native evidence.
    try:
        original_ack_bytes = canonical(ack)
        admitted_ack = json.loads(original_ack_bytes)
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        raise Hold('NATIVE_ACK_NONCANONICAL') from exc
    readback_job=json.loads(original_job_bytes)
    try:
        persisted=readback(readback_job)
    except (Exception, CancelledError) as exc:
        raise Hold('NATIVE_READBACK_UNKNOWN') from exc
    # Readback also receives a detached copy. A callback must not rebind or
    # rewrite the admitted grant/lease context after native application.
    try:
        if (canonical(readback_job) != original_job_bytes
                or canonical(apply_job) != original_job_bytes
                or canonical(job) != original_job_bytes):
            raise Hold('NATIVE_READBACK_JOB_CONTEXT_MUTATED')
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        raise Hold('NATIVE_READBACK_JOB_CONTEXT_MUTATED') from exc
    # An altered ACK means UNKNOWN after native apply, not a fresh attempt.
    try:
        if canonical(ack) != original_ack_bytes:
            raise Hold('NATIVE_ACK_MUTATED_DURING_READBACK')
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        raise Hold('NATIVE_ACK_MUTATED_DURING_READBACK') from exc
    # readback may retain a reference to the original apply payload and change
    # it after the first post-apply check. Such an alias can make the observed
    # object diverge from the bytes admitted to native apply. Never convert
    # this uncertain post-effect state into success or retry it.
    try:
        if canonical(payload) != original_payload_bytes:
            raise Hold('NATIVE_APPLY_INPUT_MUTATED_DURING_READBACK')
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        raise Hold('NATIVE_APPLY_INPUT_MUTATED_DURING_READBACK') from exc
    # Require independent effect readback to bind the same complete identity.
    # Plain transport/storage readback without native identity is not enough.
    if any(job.get(field) != value for field,value in identity.items()):
        raise Hold('JOB_IDENTITY_MUTATED')
    if (not isinstance(persisted,dict)
            or any(persisted.get(field) != identity[field] for field in required)
            or not _same_canonical_json(persisted.get('ack'), admitted_ack)
            or persisted.get('payload_sha256') != payload_digest
            or persisted.get('effect_verified') is not True):
        raise Hold('NATIVE_EFFECT_NOT_VERIFIED')
    if _explicit_negative_native_outcome(persisted, persisted_effect=True):
        raise Hold('NATIVE_EFFECT_NEGATIVE_OUTCOME')
    return {'status':'APPLIED_VERIFIED','task_id':job['task_id'],'work_key':job['work_key'],'native_ack':admitted_ack,'payload_sha256':payload_digest}
