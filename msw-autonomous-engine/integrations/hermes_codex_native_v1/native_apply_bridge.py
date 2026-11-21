"""Fail-closed Hermes native apply bridge. No Codex dispatch or queue mutation built in."""
import hashlib
import json

class Hold(RuntimeError): pass

def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()

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
    if hashlib.sha256(canonical(body)).hexdigest()!=codex_receipt.get('receipt_sha256'):
        raise Hold('CODEX_RECEIPT_DIGEST_MISMATCH')
    if hashlib.sha256(codex_receipt['text'].encode()).hexdigest()!=codex_receipt.get('response_sha256'):
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
    if not isinstance(ack,dict) or ack.get('task_id')!=job['task_id'] or ack.get('work_key')!=job['work_key'] or ack.get('payload_sha256')!=payload_digest:
        raise Hold('NATIVE_ACK_MISMATCH')
    try:
        persisted=readback(job)
    except Exception as exc:
        raise Hold('NATIVE_READBACK_UNKNOWN') from exc
    if not isinstance(persisted,dict) or persisted.get('ack')!=ack or persisted.get('payload_sha256')!=payload_digest or persisted.get('effect_verified') is not True:
        raise Hold('NATIVE_EFFECT_NOT_VERIFIED')
    return {'status':'APPLIED_VERIFIED','task_id':job['task_id'],'work_key':job['work_key'],'native_ack':ack,'payload_sha256':payload_digest}
