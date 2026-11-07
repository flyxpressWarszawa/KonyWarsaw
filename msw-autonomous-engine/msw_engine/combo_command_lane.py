"""Authenticated ChatGPT command ingress for the existing Render factory.

One small polling lane, same registered node secret, independent signed audience.
No PHP inference, new paid service, public listener, shell or financial operation.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler, ProxyHandler
from .combo_slm import LocalSLM, canonical

CENTRAL = 'https://mobilny-serwis-warszawa.pl'
NODE = 'msw-render-central-1'
ENDPOINT = CENTRAL + '/wp-content/plugins/msw-combo-render-link/transport.php'

class LinkError(RuntimeError):
    pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def wire(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)

def validate_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{8,90}', value):
        raise LinkError('request_id_invalid')
    return value

def request_headers(action, node, secret, body, stamp, nonce):
    common='\n'.join(['POST', action, node, stamp, nonce, hashlib.sha256(body).hexdigest()])
    audience='\n'.join(['MSW-COMBO-FACTORY-REQUEST-1', action, node, stamp, nonce, hashlib.sha256(body).hexdigest()])
    return {'Content-Type':'application/json','Cache-Control':'no-store',
            'X-MSWUE-Action':action,'X-MSWUE-Node':node,'X-MSWUE-Timestamp':stamp,'X-MSWUE-Nonce':nonce,
            'X-MSWUE-Signature':hmac.new(secret.encode(),common.encode(),hashlib.sha256).hexdigest(),
            'X-MSW-Factory-Audience':hmac.new(secret.encode(),audience.encode(),hashlib.sha256).hexdigest()}

def verify_response(action, node, nonce, status, raw, signature, secret):
    message='\n'.join(['MSW-COMBO-FACTORY-1', action, node, nonce, str(status), hashlib.sha256(raw).hexdigest()])
    expected=hmac.new(secret.encode(),message.encode(),hashlib.sha256).hexdigest()
    if not isinstance(signature,str) or not hmac.compare_digest(expected,signature):
        raise LinkError('response_signature_invalid')
    try:result=json.loads(raw)
    except (UnicodeError,ValueError):raise LinkError('response_json_invalid') from None
    if not isinstance(result,dict):raise LinkError('response_object_required')
    if status!=200:raise LinkError('server_rejected_command')
    return result

class Client:
    def __init__(self,environ=None,opener=None):
        env=os.environ if environ is None else environ
        if env.get('MSW_WP_URL','').rstrip('/')!=CENTRAL or env.get('MSW_NODE')!=NODE:
            raise LinkError('existing_central_node_required')
        self.secret=env.get('MSW_SECRET','')
        if not isinstance(self.secret,str) or len(self.secret)<32 or self.secret.startswith('CHANGE_'):
            raise LinkError('existing_secret_required')
        self.opener=opener or build_opener(ProxyHandler({}),NoRedirect(),HTTPSHandler(context=ssl.create_default_context()))
    def send(self, action, values):
        if action not in {'claim','complete','status','heartbeat'} or not isinstance(values,dict):
            raise LinkError('operation_invalid')
        body=wire(values).encode()
        if len(body)>48000:raise LinkError('request_limit')
        stamp,nonce=str(int(time.time())),secrets.token_hex(16)
        request=Request(ENDPOINT,data=body,headers=request_headers(action,NODE,self.secret,body,stamp,nonce),method='POST')
        try:
            try:response=self.opener.open(request,timeout=12)
            except HTTPError as e:response=e
            with response:
                raw=response.read(64001)
                if len(raw)>64000:raise LinkError('response_limit')
                return verify_response(action,NODE,nonce,response.code,raw,response.headers.get('X-MSW-Factory-Response',''),self.secret)
        except (URLError,TimeoutError,OSError):raise LinkError('transport_unavailable') from None

class CommandLane:
    def __init__(self,factory,client=None,model_factory=None):
        self.factory=factory
        self.client=client or Client()
        self.model_factory=model_factory or (lambda:LocalSLM(factory.directory,download=False))
        with self.factory.memory.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS factory_command_receipts(
                request_id TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL, lease_token TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT NOT NULL DEFAULT '', result_sha256 TEXT NOT NULL DEFAULT '')''')
    def _ack(self,row):
        values={k:row[k] for k in ('request_id','lease_token','payload_sha256','result','result_sha256')}
        ack=self.client.send('complete',values)
        read=self.client.send('status',{'request_id':row['request_id']})
        if (ack.get('stored') is not True or read.get('receipt_readback_verified') is not True
            or read.get('request_id')!=row['request_id'] or read.get('node')!=NODE
            or read.get('state') not in {'completed','failed'}
            or ack.get('result_sha256')!=row['result_sha256'] or read.get('result_sha256')!=row['result_sha256']):
            raise LinkError('central_ack_unverified')
        with self.factory.memory.connect() as db:
            db.execute("UPDATE factory_command_receipts SET state='acknowledged' WHERE request_id=? AND result_sha256=?",(row['request_id'],row['result_sha256']))
        return {'state':'command_receipt_verified','request_id':row['request_id'],
                'command_state':read['state'],'result_sha256':row['result_sha256'],'portal_live_verified':False}
    def _execute(self,action,payload,rid):
        if action=='probe':
            if payload not in ({},[]):raise LinkError('probe_payload')
            model=self.model_factory()
            try:
                result=model.classify('Serwis techniczny: czyszczenie laserowe części stalowych i maszyn.')
            finally:
                close=getattr(getattr(model,'model',None),'close',None)
                if callable(close):close()
            if result.get('inference_verified') is not True:raise LinkError('inference_not_verified')
            return {'state':'inference_returned','observed_at':int(time.time()),'slm':result,
                    'business_quality_benchmark_passed':False,'paid_api_called':False,'wordpress_compute':False}
        if action=='build':
            if not isinstance(payload,dict) or set(payload)!={'schema','site_id','site_url','paid_until','spec'} or payload['schema']!='msw.combo.render-command.v1':
                raise LinkError('build_payload')
            spec=payload['spec']
            if not isinstance(spec,dict) or spec.get('request_id')!=rid or payload['site_url']!='https://'+spec.get('domain',''):
                raise LinkError('target_identity')
            grant=self.client.send('heartbeat',{'project_id':rid})
            if grant.get('authorized') is not True or grant.get('domain')!=spec.get('domain'):
                raise LinkError('target_revoked_or_quarantined')
            return {'state':'factory_job_queued','project':self.factory.enqueue(spec),'portal_live_verified':False}
        if action=='status':
            if not isinstance(payload,dict) or set(payload)!={'project_id'}:raise LinkError('status_payload')
            return self.factory.status(validate_id(payload['project_id']))
        raise LinkError('unknown_factory_action')
    def poll(self):
        with self.factory.memory.connect() as db:
            row=db.execute("SELECT * FROM factory_command_receipts WHERE state='pending_ack' ORDER BY rowid LIMIT 1").fetchone()
        if row:return self._ack(dict(row))
        response=self.client.send('claim',{})
        if response.get('state') in {'idle','busy'}:return {'state':response['state']}
        if response.get('state')!='claimed':raise LinkError('claim_shape')
        cmd=response.get('command');lease=response.get('lease_token')
        if not isinstance(cmd,dict) or not isinstance(lease,str) or not re.fullmatch('[a-f0-9]{64}',lease):raise LinkError('claim_schema')
        rid=validate_id(cmd.get('request_id'));action=cmd.get('action');raw=cmd.get('payload')
        if action not in {'probe','build','status'} or not isinstance(raw,str) or len(raw.encode())>16000 or digest(action+'\0'+raw)!=cmd.get('payload_sha256'):
            raise LinkError('command_digest')
        with self.factory.memory.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT * FROM factory_command_receipts WHERE request_id=?',(rid,)).fetchone()
            if previous:
                if previous['payload_sha256']!=cmd['payload_sha256']:raise LinkError('request_conflict')
                raise LinkError('ambiguous_command_not_replayed')
            db.execute('INSERT INTO factory_command_receipts(request_id,payload_sha256,lease_token,state) VALUES(?,?,?,?)',(rid,cmd['payload_sha256'],lease,'inflight'))
        try:
            payload=json.loads(raw)
            result={'ok':True,'data':self._execute(action,payload,rid)}
        except Exception as e:
            code=str(e) if isinstance(e,(ValueError,LinkError)) and re.fullmatch('[a-z_]{3,100}',str(e)) else 'factory_command_failed'
            result={'ok':False,'data':{'error':code,'inference_verified':False,'portal_live_verified':False}}
        encoded=wire(result)
        if len(encoded.encode())>32000:encoded=wire({'ok':False,'data':{'error':'result_limit'}})
        with self.factory.memory.connect() as db:
            db.execute("UPDATE factory_command_receipts SET state='pending_ack',result=?,result_sha256=? WHERE request_id=?",(encoded,digest(encoded),rid))
            row=db.execute('SELECT * FROM factory_command_receipts WHERE request_id=?',(rid,)).fetchone()
        return self._ack(dict(row))
    def authorized(self,spec,cursor):
        grant=self.client.send('heartbeat',{'project_id':spec['request_id']})
        if (grant.get('authorized') is not True or grant.get('project_id')!=spec['request_id']
            or grant.get('domain')!=spec['domain'] or type(grant.get('checked_at')) is not int
            or not 0<=int(time.time())-grant['checked_at']<=60):return False
        return not 1<=cursor<=156 or grant.get('paid_authorized') is True
