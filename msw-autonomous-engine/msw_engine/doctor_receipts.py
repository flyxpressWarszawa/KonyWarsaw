"""Unblock the legacy receipt queue while preserving uncertain results.

Only a twice-confirmed missing oldest receipt can be deferred, after a recent
acknowledged receipt proves that the status endpoint is functioning. Nothing
is deleted or relabelled as delivered. Deferred entries are retried hourly.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time

from .client import Client, TransportError
from .runner import Worker


class ReceiptDoctor:
    def __init__(self, directory, *, client=None, clock=time.time):
        self.directory, self.clock = Path(directory), clock
        self.root = self.directory / 'msw-doctor-sidecar'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.audit_path = self.root / 'receipt-doctor.sqlite3'
        if self.audit_path.is_symlink(): raise ValueError('unsafe_audit_path')
        if client is None:
            base = os.environ.get('MSW_WP_URL', '').rstrip('/')
            endpoint = os.environ.get('MSW_TRANSPORT_URL', base + '/wp-content/plugins/msw-unified-engine/transport.php')
            client = Client(base, endpoint, os.environ.get('MSW_NODE', ''), os.environ.get('MSW_SECRET', ''))
        self.client = client
        self.worker = Worker(client, self.directory, max_seconds=30, memory_mb=384)
        with sqlite3.connect(self.audit_path, timeout=5) as db:
            db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at INTEGER NOT NULL, job_id TEXT NOT NULL, payload_sha256 TEXT NOT NULL, result_sha256 TEXT NOT NULL, previous_updated_at INTEGER NOT NULL, state TEXT NOT NULL)')
        os.chmod(self.audit_path, 0o600)

    def missing(self, ident):
        try:
            self.client.send('status', {'job_id': ident})
            return False
        except TransportError as error:
            if error.status == 404: return True
            raise

    def valid_control(self, controls):
        for control in controls:
            try:
                remote = self.client.send('status', {'job_id': control['job_id']})
                if remote.get('state') in {'completed', 'failed'} and remote.get('result_sha256') == control['result_sha256']:
                    return True
            except TransportError as error:
                if error.status != 404: raise
        return False

    def defer_missing_oldest(self):
        journal = self.worker.journal
        with journal.connect() as db:
            row = db.execute("SELECT * FROM receipts WHERE state='pending' ORDER BY updated_at LIMIT 1").fetchone()
            controls = db.execute("SELECT job_id,result_sha256 FROM receipts WHERE state='acknowledged' ORDER BY updated_at DESC LIMIT 3").fetchall()
        if row is None: return {'state': 'no_pending'}
        if hashlib.sha256(row['result_json'].encode()).hexdigest() != row['result_sha256']:
            return {'state': 'local_digest_mismatch'}
        if not self.missing(row['job_id']): return {'state': 'oldest_found'}
        if not self.valid_control(controls): return {'state': 'status_control_unverified'}
        if not self.missing(row['job_id']): return {'state': 'oldest_reappeared'}
        now = int(self.clock())
        with sqlite3.connect(self.audit_path, timeout=5) as audit:
            cursor = audit.execute('INSERT INTO events(at,job_id,payload_sha256,result_sha256,previous_updated_at,state) VALUES(?,?,?,?,?,?)',
                (now, row['job_id'], row['payload_sha256'], row['result_sha256'], row['updated_at'], 'prepared'))
            event = cursor.lastrowid
        with sqlite3.connect(self.audit_path, timeout=5) as audit:
            check = audit.execute('SELECT state,result_sha256 FROM events WHERE id=?', (event,)).fetchone()
            if check != ('prepared', row['result_sha256']): raise ValueError('audit_readback_failed')
        with journal.connect() as db:
            cursor = db.execute("UPDATE receipts SET state='pending_deferred',updated_at=? WHERE job_id=? AND payload_sha256=? AND result_sha256=? AND updated_at=? AND state='pending'",
                (now, row['job_id'], row['payload_sha256'], row['result_sha256'], row['updated_at']))
            changed = cursor.rowcount == 1
        with journal.connect() as db:
            after = db.execute('SELECT state,payload_sha256,result_sha256,result_json,lease_token FROM receipts WHERE job_id=?', (row['job_id'],)).fetchone()
        verified = bool(changed and after and after['state'] == 'pending_deferred'
            and after['payload_sha256'] == row['payload_sha256'] and after['result_sha256'] == row['result_sha256']
            and after['result_json'] == row['result_json'] and after['lease_token'] == row['lease_token'])
        with sqlite3.connect(self.audit_path, timeout=5) as audit:
            audit.execute('UPDATE events SET state=? WHERE id=?', ('deferred_verified' if verified else 'changed_or_unverified', event))
        return {'state': 'deferred_verified' if verified else 'changed_or_unverified',
                'receipt_preserved': verified, 'server_ack_verified': False}

    def retry_deferred(self):
        journal = self.worker.journal
        with journal.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM receipts WHERE state='pending_deferred' AND updated_at<=? ORDER BY updated_at LIMIT 3", (int(self.clock())-3600,))]
        out = []
        for receipt in rows:
            try:
                result = self.worker.acknowledge(receipt)
                out.append({'state': result['state'], 'server_ack_verified': True})
            except TransportError as error:
                # Another processor may have already committed the exact result.
                verified = False
                if error.status == 409:
                    try:
                        status = self.client.send('status', {'job_id': receipt['job_id']})
                        verified = status.get('state') in {'completed', 'failed'} and status.get('result_sha256') == receipt['result_sha256']
                    except TransportError:
                        pass
                if verified:
                    journal.mark(receipt['job_id'], 'acknowledged')
                    out.append({'state': 'acknowledged', 'server_ack_verified': True})
                else:
                    # Conditional update avoids undoing an independent ACK.
                    with journal.connect() as db:
                        db.execute("UPDATE receipts SET updated_at=? WHERE job_id=? AND state='pending_deferred' AND result_sha256=?",
                                   (int(self.clock()), receipt['job_id'], receipt['result_sha256']))
                    out.append({'state': 'retry_later', 'server_ack_verified': False})
        return out

    def step(self):
        result = {'at': int(self.clock()), 'deferral': self.defer_missing_oldest(), 'retries': self.retry_deferred()}
        print(json.dumps({'receipt_doctor': result}, separators=(',', ':')), flush=True)
        return result

    def start(self, stopping):
        def run():
            while not stopping():
                deadline = time.monotonic() + 600
                while not stopping() and time.monotonic() < deadline:
                    time.sleep(min(1, max(0, deadline-time.monotonic())))
                if stopping(): break
                try: self.step()
                except Exception as error:
                    print(json.dumps({'receipt_doctor': {'state': 'retry', 'code': type(error).__name__}}), flush=True)
        thread = threading.Thread(target=run, name='msw-doctor-receipts', daemon=True)
        thread.start()
        return thread
