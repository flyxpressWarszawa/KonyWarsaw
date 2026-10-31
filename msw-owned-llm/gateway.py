"""MSW owned-weight inference. Model output is data, never executable authority.
Only the fixed owner WordPress HTTPS origin may publish the RSA verification key.
No model downloads at request time, no cloud inference, no persistent user text.
"""
from __future__ import annotations
import base64, hashlib, json, os, re, secrets, signal, subprocess, threading, time
import urllib.error, urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

VERSION = '0.1.0'
MODEL = 'SmolLM2-135M-Instruct-Q4_K_M'
MODEL_SHA = '2e8040ceae7815abe0dcb3540b9995eaa1fa0d2ca9e797d0a635ae4433c68c2d'
MODEL_BYTES = 105454432
MODEL_REVISION = '09816acd5d99df7be770d85ea30822623dab342c'
CENTRAL = 'https://mobilny-serwis-warszawa.pl'
KEY_URL = CENTRAL + '/wp-json/msw-owned-llm/v1/public-key'
SYSTEM = 'You are an experimental assistant for MSW. Use only the supplied facts. Do not invent completed work. Give one short suggestion. Do not give commands, code or promises.'
PROBE = 'In one short sentence, explain why a repair company should record completed work.'
LIMIT = 8192

def sha(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def encode(v) -> bytes: return json.dumps(v, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
class Fault(Exception):
    def __init__(self, code, status=400): self.code, self.status = code, status
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw): raise Fault('redirect_denied', 502)

def fetch_json(url, payload=None, timeout=8, cap=32768):
    req = urllib.request.Request(url, data=None if payload is None else encode(payload),
        headers={'Accept':'application/json','Content-Type':'application/json','User-Agent':'MSW-Owned-LLM/0.1'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=timeout) as r:
        if r.status != 200 or 'application/json' not in r.headers.get('Content-Type',''): raise Fault('invalid_remote_response',502)
        raw = r.read(cap+1)
        if len(raw)>cap: raise Fault('remote_response_size',502)
        return json.loads(raw)

def parse_request(raw):
    if not isinstance(raw, bytes) or len(raw)>LIMIT: raise Fault('request_size',413)
    def unique(pairs):
        out={}
        for k,v in pairs:
            if k in out: raise ValueError('duplicate')
            out[k]=v
        return out
    try:
        d=json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(d,dict) or set(d)!={'request_id','prompt'}: raise ValueError()
        if not isinstance(d['request_id'],str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}',d['request_id']): raise ValueError()
        s=d['prompt']
        if not isinstance(s,str) or not 3<=len(s.encode('utf-8'))<=1600: raise ValueError()
        if '<|' in s or '\x00' in s: raise ValueError()
        return d
    except (ValueError,TypeError,UnicodeError,RecursionError): raise Fault('request_schema') from None

class OwnerAuth:
    def __init__(self, getter=None, clock=time.time):
        self.getter=getter or (lambda:fetch_json(KEY_URL)); self.clock=clock
        self.key=None; self.kid=None; self.until=0; self.fetch_after=0
        self.seen={}; self.lock=threading.Lock()
    def authorize(self, raw, headers):
        ts=headers.get('X-MSW-Time',''); nonce=headers.get('X-MSW-Nonce',''); sig=headers.get('X-MSW-Signature',''); kid=headers.get('X-MSW-Key','')
        if not re.fullmatch(r'\d{10}',ts) or abs(self.clock()-int(ts))>90 or not re.fullmatch(r'[a-f0-9]{32}',nonce) or not re.fullmatch(r'[a-f0-9]{64}',kid) or not 300<=len(sig)<=700: raise Fault('signature_required',401)
        now=self.clock()
        with self.lock:
            self.seen={k:v for k,v in self.seen.items() if v>now}
            if nonce in self.seen: raise Fault('replay_denied',409)
            if not self.key or self.until<=now or self.kid!=kid:
                if now<self.fetch_after: raise Fault('owner_key_wait',503)
                self.fetch_after=now+60
                try:
                    d=self.getter()
                    if not isinstance(d,dict) or d.get('schema')!='msw.owned-llm.key.v1' or d.get('origin')!=CENTRAL or d.get('enabled') is not True: raise ValueError()
                    pem=d.get('public_key','')
                    if not isinstance(pem,str) or len(pem)>1800: raise ValueError()
                    key=serialization.load_pem_public_key(pem.encode())
                    if not isinstance(key,rsa.RSAPublicKey) or not 2048<=key.key_size<=4096: raise ValueError()
                    der=key.public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
                    real=sha(der)
                    if d.get('key_id')!=real: raise ValueError()
                    self.key,self.kid,self.until=key,real,now+300
                except Exception: raise Fault('owner_key_unavailable',503) from None
            if self.kid!=kid: raise Fault('wrong_owner_key',401)
            msg=('msw-owned-llm.v1\nPOST\n/v1/generate\n'+ts+'\n'+nonce+'\n'+sha(raw)).encode()
            try: self.key.verify(base64.b64decode(sig,validate=True),msg,padding.PKCS1v15(),hashes.SHA256())
            except (ValueError,InvalidSignature): raise Fault('bad_signature',401) from None
            if len(self.seen)>=256: raise Fault('auth_capacity',429)
            self.seen[nonce]=now+180

class ModelRuntime:
    def __init__(self, root='/opt/msw'):
        self.root=Path(root); self.proc=None; self.ready=False; self.failure=None
        self.lock=threading.Lock(); self.calls=deque(); self.proof=None; self.memory_at_test=None
        self.boot_id=secrets.token_hex(12)
    def backend(self,path,payload=None): return fetch_json('http://127.0.0.1:8081'+path,payload,timeout=90,cap=65536)
    def start(self):
        try:
            path=self.root/'model.gguf'
            with path.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
            if path.stat().st_size!=MODEL_BYTES or digest!=MODEL_SHA: raise Fault('weight_integrity')
            args=[str(self.root/'llama/llama-server'),'-m',str(path),'--host','127.0.0.1','--port','8081','-c','512','-np','1','-t','1','-tb','1','-b','64','-ub','64','-n','64','--no-webui','--log-disable']
            env=os.environ.copy();env['LD_LIBRARY_PATH']=str(self.root/'llama')
            self.proc=subprocess.Popen(args,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            for _ in range(180):
                if self.proc.poll() is not None: raise Fault('backend_exited')
                try:
                    if self.backend('/health').get('status')=='ok': break
                except Exception: pass
                time.sleep(1)
            else: raise Fault('backend_load_timeout')
            with self.lock:
                self.proof=self._generate(PROBE,32)
                self.proof.update({'prompt':PROBE,'synthetic_test':True,'business_quality_evaluated':False,'performed_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'boot_id':self.boot_id})
            self.ready=True
            print(json.dumps({'event':'model_self_test','model':MODEL,'weight_sha256':MODEL_SHA,'proof':self.proof}),flush=True)
        except Exception as exc:
            self.failure=exc.code if isinstance(exc,Fault) else 'startup_failed'
            print(json.dumps({'event':'model_start_failed','code':self.failure}),flush=True)
    def _generate(self,prompt,maximum=64):
        full='<|im_start|>system\n'+SYSTEM+'<|im_end|>\n<|im_start|>user\n'+prompt+'<|im_end|>\n<|im_start|>assistant\n'
        tok=self.backend('/tokenize',{'content':full,'add_special':False}).get('tokens')
        if not isinstance(tok,list) or len(tok)>420: raise Fault('context_too_long',413)
        started=time.monotonic()
        result=self.backend('/completion',{'prompt':full,'n_predict':maximum,'temperature':0.2,'seed':42,'cache_prompt':False,'stop':['<|im_end|>']})
        text=result.get('content');n=result.get('tokens_predicted')
        if not isinstance(text,str) or not text.strip() or type(n) is not int or not 0<n<=maximum: raise Fault('invalid_generation',502)
        return {'text':text.strip(),'tokens_generated':n,'prompt_tokens':len(tok),'elapsed_ms':round((time.monotonic()-started)*1000),'runtime':'llama.cpp-b11979-local-cpu','model':MODEL,'weight_sha256':MODEL_SHA,'training_performed':False,'actions_executed':False}
    def generate(self,prompt):
        if not self.ready or not self.proc or self.proc.poll() is not None: raise Fault('model_not_ready',503)
        if not self.lock.acquire(blocking=False): raise Fault('model_busy',429)
        try:
            now=time.monotonic()
            while self.calls and self.calls[0]<now-60: self.calls.popleft()
            if len(self.calls)>=4: raise Fault('rate_limit',429)
            self.calls.append(now)
            return self._generate(prompt)
        finally:self.lock.release()
    def status(self):
        alive=bool(self.proc and self.proc.poll() is None)
        return {'schema':'msw.owned-llm.status.v1','version':VERSION,'ready':self.ready and alive,'failure':self.failure,'model':MODEL,'weights':{'sha256':MODEL_SHA,'bytes':MODEL_BYTES,'revision':MODEL_REVISION,'origin':'HuggingFaceTB/SmolLM2-135M-Instruct','quantizer':'bartowski','license':'Apache-2.0'},'context_tokens':512,'maximum_output_tokens':64,'language_scope':'primarily_English; Polish_business_quality_not_validated','authentication':'RSA_SHA256_from_fixed_WordPress_HTTPS_identity','central_origin':CENTRAL,'public_generation':False,'external_inference_API':False,'weight_training':False,'autonomous_code_changes':False,'business_autonomy_verified':False,'self_test':self.proof}
    def stop(self):
        if self.proc and self.proc.poll() is None:self.proc.terminate()

RUNTIME=None
AUTH=OwnerAuth()
class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.0'
    def log_message(self,*a):pass
    def setup(self):super().setup();self.connection.settimeout(10)
    def reply(self,status,obj):
        raw=encode(obj);self.send_response(status)
        for k,v in {'Content-Type':'application/json; charset=utf-8','Content-Length':str(len(raw)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Robots-Tag':'noindex'}.items():self.send_header(k,v)
        self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        if self.path in ('/health','/v1/status'):
            self.reply(200,RUNTIME.status())
        else:self.reply(404,{'error':'not_found'})
    def do_POST(self):
        try:
            if self.path!='/v1/generate':raise Fault('not_found',404)
            if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Encoding','identity')!='identity':raise Fault('encoding_denied',415)
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise Fault('content_type',415)
            sizes=self.headers.get_all('Content-Length',[])
            if len(sizes)!=1 or not sizes[0].isdigit():raise Fault('content_length',411)
            n=int(sizes[0])
            if not 1<=n<=LIMIT:raise Fault('request_size',413)
            raw=self.rfile.read(n)
            if len(raw)!=n:raise Fault('incomplete_body')
            d=parse_request(raw);AUTH.authorize(raw,self.headers)
            r=RUNTIME.generate(d['prompt']);r.update({'schema':'msw.owned-llm.result.v1','request_id':d['request_id'],'request_sha256':sha(raw),'wordPress_result_saved':False})
            self.reply(200,r)
        except Fault as e:self.reply(e.status,{'error':e.code})
        except Exception:self.reply(503,{'error':'generation_unavailable'})
class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,*a,**kw):super().__init__(*a,**kw);self.slots=threading.BoundedSemaphore(6)
    def process_request(self,request,address):
        if not self.slots.acquire(False):self.shutdown_request(request);return
        try:super().process_request(request,address)
        except Exception:self.slots.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()

def main():
    global RUNTIME
    RUNTIME=ModelRuntime(os.getenv('MSW_RUNTIME_ROOT','/opt/msw'))
    server=Server(('0.0.0.0',int(os.getenv('PORT','10000'))),Handler)
    threading.Thread(target=RUNTIME.start,daemon=True).start()
    def stop(*_):RUNTIME.stop();raise SystemExit(0)
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:server.serve_forever()
    finally:RUNTIME.stop();server.server_close()
if __name__=='__main__':main()
