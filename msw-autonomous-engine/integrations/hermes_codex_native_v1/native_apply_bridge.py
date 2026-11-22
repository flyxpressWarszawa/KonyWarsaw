"""Fail-closed Hermes native apply bridge. No Codex dispatch or queue mutation built in."""
import hashlib
import json

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
    if record.get('error') not in (None, '', False):
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
    # The persisted record must say that the NATIVE effect was applied.
    # An absent state plus effect_verified=True would let a storage-only
    # readback masquerade as native completion. Fail closed, do not retry.
    if persisted_effect and not any(key in record for key in ('status', 'state', 'outcome')):
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
    identity={k:job[k] for k in required}
    payload={'identity':identity,'codex_receipt_sha256':codex_receipt['receipt_sha256'],'result':codex_receipt['text']}
    payload_digest=hashlib.sha256(canonical(payload)).hexdigest()
    try:
        ack=apply_once(job,payload)
    except Exception as exc:
        raise Hold('NATIVE_APPLY_UNKNOWN_OUTCOME') from exc
    # A matching task/work key and payload hash are insufficient: a native
    # ACK must also bind the original root, request, and parent, not merely
    # describe some persisted bytes. The authenticated receiver is responsible
    # for issuing this complete record; this check is not a signature verifier.
    if (not isinstance(ack,dict)
            or any(ack.get(field) != job[field] for field in required)
            or ack.get('payload_sha256') != payload_digest):
        raise Hold('NATIVE_ACK_MISMATCH')
    if _explicit_negative_native_outcome(ack):
        raise Hold('NATIVE_ACK_NEGATIVE_OUTCOME')
    try:
        persisted=readback(job)
    except Exception as exc:
        raise Hold('NATIVE_READBACK_UNKNOWN') from exc
    # Require independent effect readback to bind the same complete identity.
    # Plain transport/storage readback without native identity is not enough.
    if (not isinstance(persisted,dict)
            or any(persisted.get(field) != job[field] for field in required)
            or not _same_canonical_json(persisted.get('ack'), ack)
            or persisted.get('payload_sha256') != payload_digest
            or persisted.get('effect_verified') is not True):
        raise Hold('NATIVE_EFFECT_NOT_VERIFIED')
    if _explicit_negative_native_outcome(persisted, persisted_effect=True):
        raise Hold('NATIVE_EFFECT_NEGATIVE_OUTCOME')
    return {'status':'APPLIED_VERIFIED','task_id':job['task_id'],'work_key':job['work_key'],'native_ack':ack,'payload_sha256':payload_digest}
