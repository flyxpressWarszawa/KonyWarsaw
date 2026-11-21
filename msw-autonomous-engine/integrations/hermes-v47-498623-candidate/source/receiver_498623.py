"""One-step receiver core for the existing 498623 owner.

Host methods are trusted owner callbacks, not supplied by a report/model.
The caller must hold the existing business lock throughout advance().
Installation into the live owner/lifecycle is a separate release step.
"""
import copy
import profile_498623 as p
import final_job_guard_498623 as final_guard


def advance(host, journal):
    host.check_fences()
    state = journal.read()
    phase = state['phase']
    if phase in {'report_admitted', 'ack_uncertain'}:
        if phase == 'report_admitted' and journal.begin_ack():
            host.check_fences()
            # The committed intent is the fence even if this call raises.
            host.ack_original()
        observed = host.read_original_ack()
        if observed is None:
            return {'state': 'ack_uncertain', 'dispatch_repeated': False}
        host.check_fences()
        return {'state': journal.observe_ack(observed)['phase']}
    if phase == 'ack_observed':
        request = host.build_native_request()
        host.check_fences()
        # Commit our one-shot intent BEFORE calling any owner lifecycle CAS.
        # An interrupted or uncertain reservation never permits re-dispatch.
        # The owner callback must use native_job_barrier.reserve against the
        # ORIGINAL jobs row, not fabricate a matching request in memory.
        reserve = getattr(host, 'reserve_existing_native_effect', None)
        p.require(callable(reserve), 'native_lifecycle_reservation_required')
        if journal.reserve_native(request):
            host.check_fences()
            saved = reserve(copy.deepcopy(request))
            p.require(isinstance(saved, dict) and p.digest(saved) == p.digest(request),
                      'native_lifecycle_reservation_mismatch')
            host.check_fences()
            host.apply_native(copy.deepcopy(request))
        return {'state': 'native_uncertain'}
    if phase == 'native_uncertain':
        request = state['native_request']
        p.require(isinstance(request, dict), 'persisted_native_request_required')
        receipt = host.read_native(copy.deepcopy(request))
        if receipt is None:
            return {'state': 'native_uncertain', 'dispatch_repeated': False}
        proof = host.verify_native_receipt(copy.deepcopy(request), copy.deepcopy(receipt))
        host.check_fences()
        return {'state': journal.observe_native(request, receipt, proof)['phase']}
    if phase == 'native_observed':
        # A native-session receipt proves one effect, not completion of the
        # authoritative owner jobs row. Read under the existing business lock.
        host.check_fences()
        observation = host.read_final_job()
        if observation is None:
            return {'state': 'native_observed', 'requires_final_job_readback': True,
                    'final_job_verified': False}
        p.require(isinstance(observation, dict)
                  and set(observation) == {'job', 'envelope'},
                  'final_owner_readback_required')
        result = final_guard.verify(observation['job'], observation['envelope'], state)
        host.check_fences()
        return result
    raise p.AdmissionError('unknown_receiver_phase')
