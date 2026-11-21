"""Bridge the receiver to trusted owner and BusinessChatHost callbacks.

Construction and callbacks belong to the source-pinned owner. The owner grant
is read under the existing business lock and revalidated before effects.
"""
import copy
import profile_498623 as p
from owner_grant_adapter_498623 import read_verified_grant


class BusinessHostBridge:
    def __init__(self, host, original_task, transport_request, *, check_fences,
                 owner_readback, read_admission, read_current_run,
                 build_native_request, reserve_native_request, read_final_job):
        p.require(p.digest(original_task) == p.ORIGINAL_TASK_SHA, 'original_task_changed')
        expected = {'root_job_id': p.ROOT, 'task_id': p.TASK, 'work_key': p.WORK_KEY,
                    'request_id': p.ROOT + ':chat:2', 'conversation_id': p.CONVERSATION,
                    'request_binding_sha256': p.BINDING_SHA}
        p.require(isinstance(transport_request, dict)
                  and all(transport_request.get(k) == v for k, v in expected.items()), 'transport_request_mismatch')
        p.require(getattr(host, 'enabled', False) is True
                  and getattr(host, 'verification_scope', None) == 'native_hermes', 'native_host_required')
        p.require(callable(build_native_request), 'trusted_native_request_builder_required')
        # The source-pinned owner must bind this to native_job_barrier.reserve.
        p.require(callable(reserve_native_request), 'trusted_native_lifecycle_reserver_required')
        p.require(callable(read_final_job), 'trusted_final_readback_required')
        self.host = host
        self.task = copy.deepcopy(original_task)
        self.request = copy.deepcopy(transport_request)
        self.check_fences = check_fences
        self.owner_readback = owner_readback
        self.read_admission = read_admission
        self.read_current_run = read_current_run
        self._build_native_request = build_native_request
        self._reserve_native_request = reserve_native_request
        self.read_final_job = read_final_job
        self.grant_receipt = self._refresh_grant()

    def _refresh_grant(self):
        current = read_verified_grant(owner_readback=self.owner_readback,
                                      read_admission=self.read_admission,
                                      read_current_run=self.read_current_run,
                                      check_fences=self.check_fences)
        if hasattr(self, 'grant_receipt'):
            p.require(all(current.get(k) == self.grant_receipt.get(k) for k in (
                'root_job_id', 'task_id', 'admission_sha256', 'grant_sha256', 'run_id')),
                'owner_grant_changed')
        return current

    def build_native_request(self):
        self._refresh_grant()
        return self._build_native_request()

    def reserve_existing_native_effect(self, request):
        self._refresh_grant()
        saved = self._reserve_native_request(copy.deepcopy(request))
        p.require(isinstance(saved, dict) and p.digest(saved) == p.digest(request),
                  'native_lifecycle_reservation_mismatch')
        return copy.deepcopy(saved)

    def ack_original(self):
        self._refresh_grant()
        request = copy.deepcopy(self.request)
        request.update(result=self.task['result'], result_sha256=self.task['result_sha256'])
        return self.host.transport('ack', request)

    def read_original_ack(self):
        self._refresh_grant()
        observed = self.host.transport('read', copy.deepcopy(self.request))
        p.require(isinstance(observed, dict)
                  and observed.get('schema') == 'msw.agent.chat-observation.v1'
                  and observed.get('request_binding_verified') is True
                  and all(observed.get(k) == self.request[k] for k in (
                      'root_job_id', 'task_id', 'work_key', 'request_id', 'conversation_id', 'request_binding_sha256')),
                  'original_observation_mismatch')
        if observed.get('state') == 'acknowledged':
            return observed
        p.require(observed.get('state') == 'reported'
                  and observed.get('outcome') == 'blocked'
                  and observed.get('result') == self.task['result']
                  and observed.get('result_sha256') == p.RESULT_SHA
                  and observed.get('result_integrity_verified') is True
                  and observed.get('storage_ack_committed') is False, 'original_report_changed')
        return None

    def apply_native(self, request):
        self._refresh_grant()
        return self.host.apply_native(copy.deepcopy(request))

    def read_native(self, request):
        self._refresh_grant()
        observed = self.host.read_native(copy.deepcopy(request))
        p.require(isinstance(observed, dict)
                  and observed.get('schema') == 'msw.agent.native-application.receipt.v1'
                  and all(observed.get(k) == request[k] for k in (
                      'idempotency_key', 'root_job_id', 'task_id', 'native_request_sha256', 'result_sha256')),
                  'native_observation_mismatch')
        if observed.get('state') == 'completed':
            return observed
        p.require(observed.get('state') in {'pending', 'uncertain'}, 'native_observation_state')
        return None

    def verify_native_receipt(self, request, receipt):
        self._refresh_grant()
        return self.host.verify_native_receipt(copy.deepcopy(request), copy.deepcopy(receipt))
