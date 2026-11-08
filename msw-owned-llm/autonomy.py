"""Extend signed inference without changing authentication or business permissions.
WordPress owns admission, persistence and application of priority candidates.
"""
import json
import threading
import time
import gateway
import codeforge
import advisory
import hf_bridge
import leadtriage

BaseRuntime = gateway.ModelRuntime

class AutonomousRuntime(BaseRuntime):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.code_proof = None
        self.triage_proof = None
        self.polish_triage_proof = None
        self.hf_search_proof = None
        self.hf_bridge = hf_bridge.HuggingFaceBridge()

    def _check_hf_bridge(self):
        verified = self.hf_bridge.verify()
        print(json.dumps({'event':'huggingface_bridge_check', 'verified':verified,
                          'state':self.hf_bridge.status()['state']}), flush=True)
        if verified:
            try:
                result = self.hf_bridge.search('models','SmolLM2-135M-Instruct',3)
                self.hf_search_proof = {'verified':True,'result_count':len(result['results'])}
                print(json.dumps({'event':'huggingface_search_check', **self.hf_search_proof}), flush=True)
            except hf_bridge.BridgeError:
                self.hf_search_proof = {'verified':False}
                print(json.dumps({'event':'huggingface_search_check','verified':False}), flush=True)

    def start(self):
        threading.Thread(target=self._check_hf_bridge, daemon=True).start()
        super().start()
        if not self.ready:
            return
        try:
            with self.lock:
                p = codeforge.generate(self, 'reliability')
                p.update({'synthetic_test': True, 'actions_executed': False,
                          'performed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                          'boot_id': self.boot_id})
                self.code_proof = p
            print(json.dumps({'event':'code_candidate_self_test','proof':p}), flush=True)
        except Exception:
            self.code_proof = {'error':'candidate_generation_failed','synthetic_test':True}
            print(json.dumps({'event':'code_candidate_self_test_failed'}), flush=True)
        try:
            with self.lock:
                example = leadtriage.PREFIX + json.dumps({'description':'Repair a broken electrical socket.'})
                p = leadtriage.generate(self, example)
                self.triage_proof = {'synthetic_test':True, 'observed_category':p['category'],
                                     'expected_category':'electrical', 'matched':p['category']=='electrical',
                                     'elapsed_ms':p['elapsed_ms'], 'boot_id':self.boot_id}
            print(json.dumps({'event':'lead_triage_self_test','proof':self.triage_proof}), flush=True)
        except Exception:
            self.triage_proof = {'error':'triage_self_test_failed','synthetic_test':True}
            print(json.dumps({'event':'lead_triage_self_test_failed'}), flush=True)
        try:
            with self.lock:
                example = leadtriage.PREFIX + json.dumps({'description':'Awaria gniazdka elektrycznego.'},ensure_ascii=False)
                p = leadtriage.generate(self, example)
                self.polish_triage_proof = {'synthetic_test':True, 'observed_category':p['category'],
                                            'expected_category':'electrical', 'matched':p['category']=='electrical',
                                            'elapsed_ms':p['elapsed_ms'], 'boot_id':self.boot_id}
            print(json.dumps({'event':'lead_triage_polish_self_test','proof':self.polish_triage_proof}), flush=True)
        except Exception:
            self.polish_triage_proof = {'error':'polish_triage_self_test_failed','synthetic_test':True}
            print(json.dumps({'event':'lead_triage_polish_self_test_failed'}), flush=True)

    def _generate(self, prompt, maximum=64):
        if prompt.startswith(leadtriage.PREFIX):
            try:
                result = leadtriage.generate(self, prompt)
            except ValueError:
                raise gateway.Fault('triage_rejected', 502) from None
            result.update({'runtime':'llama.cpp-b11979-local-cpu','model':gateway.MODEL,
                'weight_sha256':gateway.MODEL_SHA,'training_performed':False,'actions_executed':False})
            return result
        if not prompt.startswith(advisory.PREFIX):
            return super()._generate(prompt, maximum)
        try:
            result = advisory.generate(self, prompt)
        except ValueError:
            raise gateway.Fault('advisory_quality_rejected', 502) from None
        result.update({'runtime':'llama.cpp-b11979-local-cpu','model':gateway.MODEL,
            'weight_sha256':gateway.MODEL_SHA,'training_performed':False,'actions_executed':False})
        return result

    def generate(self, prompt):
        if not prompt.startswith(codeforge.PREFIX):
            return super().generate(prompt)
        objective = prompt[len(codeforge.PREFIX):]
        if objective not in codeforge.OBJECTIVES:
            raise gateway.Fault('code_objective', 400)
        if not self.ready or not self.proc or self.proc.poll() is not None:
            raise gateway.Fault('model_not_ready', 503)
        if not self.lock.acquire(False):
            raise gateway.Fault('model_busy', 429)
        try:
            now = time.monotonic()
            while self.calls and self.calls[0] < now-60:
                self.calls.popleft()
            if len(self.calls) >= 4:
                raise gateway.Fault('rate_limit', 429)
            self.calls.append(now)
            try:
                result = codeforge.generate(self, objective)
            except ValueError:
                raise gateway.Fault('code_candidate_invalid', 502) from None
            result.update({'runtime':'llama.cpp-b11979-local-cpu','model':gateway.MODEL,
                'weight_sha256':gateway.MODEL_SHA,'training_performed':False,'actions_executed':False})
            return result
        finally:
            self.lock.release()

    def search_huggingface(self, kind, query, limit):
        try:
            return self.hf_bridge.search(kind, query, limit)
        except hf_bridge.BridgeError as error:
            raise gateway.Fault(error.args[0] if error.args else 'bridge_unavailable', 503) from None

    def status(self):
        d = super().status()
        d.update({'version':'0.3.0','code_candidate_generation':True,
            'code_domain':'one_constrained_priority_function',
            'code_self_test':self.code_proof,'server_source_auto_overwrite':False,
            'advisory_prompt_normalization':True,
            'advisory_mode':'model_selects_one_of_four_read_only_checks_not_freeform_business_planning',
            'advisory_catalog':advisory.CATALOG,
            'advisory_catalog_origin':'human_authored_not_model_generated',
            'advisory_quality_gate':'exact_catalog_schema; selection_optimality_not_verified',
            'huggingface_bridge':self.hf_bridge.status(),
            'huggingface_search_self_test':self.hf_search_proof,
            'lead_triage_available':True, 'lead_triage_self_test':self.triage_proof, 'lead_triage_polish_self_test':self.polish_triage_proof,
            'lead_triage_quality':'not_validated_for_polish_business_use'})
        return d

if __name__ == '__main__':
    gateway.ModelRuntime = AutonomousRuntime
    gateway.main()
