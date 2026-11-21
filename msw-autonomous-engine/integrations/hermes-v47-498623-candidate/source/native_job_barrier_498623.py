"""Reserve native application using the existing lifecycle's guarded jobs CAS.

The existing owner lock must be held. This never calls apply_native, changes
the original report, or relaxes a lifecycle guard. The source-pinned owner
must already have admitted the supplement and persisted the original ACK.
"""
import copy
import profile_498623 as p


def reserve(agent, lifecycle, journal, *, expected_request):
    # The trusted BusinessChatHost receiver supplies the exact intended bytes.
    # Reject a mismatch BEFORE creating any native jobs-CAS reservation.
    p.require(isinstance(expected_request, dict), 'native_request_from_receiver_required')
    state = journal.read()
    p.require(state.get('phase') in {'ack_observed', 'native_uncertain'}, 'ack_readback_required')
    job = lifecycle._fresh(agent, p.ROOT)
    p.require(job.get('lane') == 'business' and job.get('id') == p.ROOT
              and job.get('state') in {'chat_storage_acked', 'native_apply_reserved', 'native_apply_uncertain'},
              'existing_native_job_state_required')
    envelope = lifecycle._envelope(job)  # Actual legacy digest/identity/owner guards.
    step = envelope['steps'][-1]
    p.require(envelope.get('verification_scope') == 'native_hermes'
              and step.get('storage_ack') is True
              and isinstance(step.get('owner_supplement'), dict)
              and step['context'].get('task_id') == p.TASK
              and step['context'].get('work_key') == p.WORK_KEY, 'native_job_binding')
    request = lifecycle.native_request(envelope)
    p.require(request.get('task_id') == p.TASK and request.get('root_job_id') == p.ROOT
              and request.get('result_sha256') == state.get('report_sha256'), 'native_report_changed')
    p.require(p.digest(expected_request) == p.digest(request),
              'native_request_from_receiver_mismatch')
    if job['state'] == 'chat_storage_acked':
        step['native_request'] = copy.deepcopy(request)
        job = lifecycle._write(agent, job, 'native_apply_reserved', envelope)
    else:
        saved = step.get('native_request')
        p.require(isinstance(saved, dict) and p.digest(saved) == p.digest(request),
                  'saved_native_request_changed')
    if job['state'] == 'native_apply_reserved':
        job = lifecycle._write(agent, job, 'native_apply_uncertain', envelope)
    fresh = lifecycle._fresh(agent, p.ROOT)
    verified = lifecycle._envelope(fresh)
    readback_request = verified['steps'][-1].get('native_request')
    p.require(fresh['state'] == 'native_apply_uncertain'
              and isinstance(readback_request, dict)
              and p.digest(readback_request) == p.digest(request), 'native_job_readback_failed')
    return copy.deepcopy(request)
