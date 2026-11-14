"""One exact owner-approved pure compute job through the EXISTING signed WP queue.

Never enrolls a node, changes permissions, sends model requests, or claims a
second job after an uncertain first claim. Staging service identity stays explicit.
"""
from __future__ import annotations
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3

SERVICE = 'srv-dbnodkbrjlhs739drc5g'
NODE = 'msw-render-auxseo-20261107-2032'
ORIGIN = 'https://mobilny-serwis-warszawa.pl'
TRANSPORT = ORIGIN + '/wp-content/plugins/msw-unified-engine/transport.php'
JOB = '2cd028d9-c636-20e5-40e4-a02dffce0da6'
PAYLOAD_SHA = '7c33215375db8efb91c8d70c6f693db24090ad0e39ca355f50d36b9099000fcc'
KIND = 'research.rank'
ROOT = Path('/tmp/msw-aux-seo/native-python-proof')


class ProofError(RuntimeError):
    pass


def connect(root):
    if root.is_symlink() or (root / 'admission.sqlite3').is_symlink():
        raise ProofError('private_storage_symlink')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    path = root / 'admission.sqlite3'
    db = sqlite3.connect(path, timeout=5, isolation_level=None)
    os.chmod(path, 0o600)
    db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE IF NOT EXISTS proof(id TEXT PRIMARY KEY,phase TEXT NOT NULL,result TEXT)')
    return db


def run_exact_once(client, worker, root):
    """Persist intent before claim; no automatic redispatch, even on restart."""
    with closing(connect(root)) as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT phase,result FROM proof WHERE id=?',(JOB,)).fetchone()
        if old:
            db.execute('COMMIT')
            if old[0] == 'completed':
                saved = json.loads(old[1])
                status = client.send('status', {'job_id': JOB})
                if (status.get('id') != JOB or status.get('state') != 'completed'
                        or status.get('payload_sha256') != PAYLOAD_SHA
                        or status.get('result_sha256') != saved.get('result_sha256')):
                    raise ProofError('replay_readback_mismatch')
                return {**saved, 'reused': True}
            return {'state': 'prior_admission_requires_review', 'job_id': JOB,
                    'server_ack_verified': False, 'central_runtime_executed': False}
        db.execute("INSERT INTO proof VALUES(?,'claim_intent',NULL)",(JOB,))
        db.execute('COMMIT')
        claimed = client.send('claim', {'capabilities': [KIND]})
        if not isinstance(claimed, dict) or claimed.get('state') != 'claimed':
            raise ProofError('no_exact_claim_no_retry')
        job, lease = claimed.get('job'), claimed.get('lease_token')
        if (not isinstance(job,dict) or job.get('id') != JOB or job.get('kind') != KIND
                or job.get('payload_sha256') != PAYLOAD_SHA
                or not isinstance(job.get('payload'),str)
                or hashlib.sha256(job['payload'].encode()).hexdigest() != PAYLOAD_SHA
                or not isinstance(lease,str) or not 20 <= len(lease) <= 256):
            raise ProofError('claimed_job_mismatch_no_execution')
        receipt = worker.execute(job, lease)
        if receipt.get('job_id') != JOB or receipt.get('payload_sha256') != PAYLOAD_SHA:
            raise ProofError('execution_receipt_mismatch')
        ack = worker.acknowledge(receipt)
        if ack.get('job_id') != JOB or ack.get('state') != 'completed' or ack.get('server_ack_verified') is not True:
            raise ProofError('server_ack_unverified')
        result = {'state':'completed','job_id':JOB,'kind':KIND,'payload_sha256':PAYLOAD_SHA,
                  'result_sha256':receipt['result_sha256'],'server_ack_verified':True,
                  'executor_node':NODE,'executor_service':SERVICE,'central_runtime_executed':False,
                  'reused':False,'model_calls':0,'public_content_changed':False}
        raw = json.dumps(result,sort_keys=True,separators=(',',':'))
        db.execute("UPDATE proof SET phase='completed',result=? WHERE id=?",(raw,JOB))
        with closing(connect(root)) as other:
            if other.execute('SELECT phase,result FROM proof WHERE id=?',(JOB,)).fetchone() != ('completed',raw):
                raise ProofError('local_proof_readback_failed')
        return result


def run_live_once():
    if os.environ.get('MSW_PYTHON_LIVE_QUEUE_PROOF') != '1':
        return {'state':'disabled'}
    if os.environ.get('RENDER_SERVICE_ID') != SERVICE or os.environ.get('MSW_NODE') != NODE:
        return {'state':'existing_auxiliary_identity_missing','queue_claimed':False}
    if (os.environ.get('MSW_WP_URL','').rstrip('/') != ORIGIN
            or os.environ.get('MSW_TRANSPORT_URL',TRANSPORT) != TRANSPORT
            or len(os.environ.get('MSW_SECRET','')) < 32):
        return {'state':'existing_signed_transport_missing','queue_claimed':False}
    if not os.path.ismount(ROOT.parent):
        return {'state':'persistent_mount_unverified','queue_claimed':False}
    # Keep compute discovery and auxiliary hooks restricted to pure functions.
    os.environ['MSW_RUNTIME_MODE']='render-free'
    os.environ['MSW_AUTONOMOUS_AGENT']='0'
    try:
        from .client import Client
        from .runner import Worker
        client = Client(ORIGIN, TRANSPORT, NODE, os.environ['MSW_SECRET'])
        worker = Worker(client, ROOT / 'worker', max_seconds=30, memory_mb=256)
        return run_exact_once(client, worker, ROOT)
    except ProofError as error:
        return {'state':'blocked_no_retry','code':str(error),'server_ack_verified':False}
    except Exception as error:
        return {'state':'uncertain_no_retry','error_class':type(error).__name__,
                'server_ack_verified':False}
