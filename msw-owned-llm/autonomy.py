"""Extend signed inference without changing authentication or business permissions.
WordPress owns admission, persistence and application of priority candidates.
"""
import json
import time
import gateway
import codeforge
import advisory

BaseRuntime = gateway.ModelRuntime

class AutonomousRuntime(BaseRuntime):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.code_proof = None

    def start(self):
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

    def _generate(self, prompt, maximum=64):
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

    def status(self):
        d = super().status()
        d.update({'version':'0.2.1','code_candidate_generation':True,
            'code_domain':'one_constrained_priority_function',
            'code_self_test':self.code_proof,'server_source_auto_overwrite':False,
            'advisory_prompt_normalization':True,
            'advisory_quality_gate':'echo_incomplete_and_mutating_advice_rejected; not factual validation'})
        return d

if __name__ == '__main__':
    gateway.ModelRuntime = AutonomousRuntime
    gateway.main()
