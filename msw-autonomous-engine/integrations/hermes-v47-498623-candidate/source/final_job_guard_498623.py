"""Final owner-native job readback for root 498623.

This is a read-only acceptance guard, not an ACK, native dispatcher or a
replacement queue. The authenticated owner supplies the existing jobs-CAS
snapshot under the business lock. A journal native receipt alone is not job
completion.
"""
import profile_498623 as p


def verify(job, envelope, state):
    p.require(isinstance(job, dict) and isinstance(envelope, dict)
              and isinstance(state, dict), 'final_owner_snapshot_required')
    p.require(job.get('id') == p.ROOT and job.get('lane') == 'business'
              and job.get('state') == 'completed'
              and isinstance(job.get('result'), str)
              and p.sha_text(job['result']) == job.get('sha256'),
              'final_job_not_completed')
    value = envelope.get('collaboration', envelope)
    p.require(isinstance(value, dict) and value.get('root_job_id') == p.ROOT
              and type(value.get('step_index')) is int and value['step_index'] == 2
              and isinstance(value.get('steps'), list) and len(value['steps']) == 2
              and p.digest(p.strict_json(job['result'])) == p.digest(envelope),
              'final_envelope_binding')
    step = value['steps'][1]
    p.require(isinstance(step, dict) and isinstance(step.get('context'), dict)
              and step['context'].get('task_id') == p.TASK
              and step['context'].get('work_key') == p.WORK_KEY
              and step.get('storage_ack') is True,
              'final_step_identity')
    supplement = step.get('owner_supplement')
    p.require(isinstance(supplement, dict)
              and isinstance(supplement.get('report'), dict)
              and supplement['report'].get('result_sha256') == state.get('report_sha256')
              and supplement.get('receipt') is not None,
              'final_supplement_missing')
    native = state.get('native')
    p.require(state.get('phase') == 'native_observed' and isinstance(native, dict)
              and all(isinstance(native.get(k), dict) and isinstance(step.get(field), dict)
                      and p.digest(step[field]) == p.digest(native[k])
                      for field, k in (('native_request', 'request'),
                                       ('native_receipt', 'receipt'),
                                       ('native_verification', 'proof')))
              and isinstance(state.get('native_sha256'), str)
              and len(state['native_sha256']) == 64,
              'final_native_proof_missing')
    return {'state': 'completed', 'root_job_id': p.ROOT, 'task_id': p.TASK,
            'final_job_sha256': job['sha256'],
            'admission_sha256': state['admission_sha256'],
            'native_receipt_sha256': state['native_sha256'],
            'final_job_verified': True, 'queue_writes': 0, 'model_calls': 0}
