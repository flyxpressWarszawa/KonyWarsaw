"""Independent Render-side WordPress probe and scoped hosting-doctor bridge.

The probe does not depend on WordPress queues. The repair bridge is usable only
when its separately authorized OAuth grant and the hosting PHP endpoint exist.
It cannot repair a provider-wide outage or serve as a copy of the website.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPSHandler

from .client import NoRedirect

ORIGIN = 'https://mobilny-serwis-warszawa.pl'
MCP = ORIGIN + '/msw-cpanel-direct/mcp.php'
TOKEN = ORIGIN + '/msw-cpanel-direct/index.php?route=token'


def request(url, *, data=None, headers=None, timeout=12):
    opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    req = Request(url, data=data, headers=headers or {}, method='POST' if data is not None else 'GET')
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read(262145)
            if len(raw) > 262144:
                return {'state': 'response_limit'}
            return {'state': 'http', 'status': response.status, 'body': raw}
    except HTTPError as error:
        return {'state': 'http', 'status': error.code, 'body': b''}
    except URLError as error:
        cause = error.reason
        if isinstance(cause, socket.gaierror): kind = 'dns'
        elif isinstance(cause, ssl.SSLError): kind = 'tls'
        elif isinstance(cause, (TimeoutError, socket.timeout)): kind = 'timeout'
        else: kind = 'connection'
        return {'state': kind}
    except (TimeoutError, OSError):
        return {'state': 'connection'}


class BridgeGrant:
    """OAuth rotation is journalled before POST; uncertain refresh is never replayed."""
    def __init__(self, directory, *, env=None, send=request, clock=time.time):
        self.path = Path(directory) / 'doctor-bridge-grant.json'
        self.env, self.send, self.clock = os.environ if env is None else env, send, clock

    def _store(self, value):
        raw = json.dumps(value, separators=(',', ':'), allow_nan=False).encode()
        tmp = self.path.with_suffix('.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.chmod(tmp, 0o600); os.replace(tmp, self.path)
        directory_fd = os.open(self.path.parent, os.O_RDONLY)
        try: os.fsync(directory_fd)
        finally: os.close(directory_fd)

    def load(self):
        if self.path.is_symlink():
            return {}
        if self.path.exists():
            try:
                result = json.loads(self.path.read_text())
                valid = (isinstance(result, dict) and type(result.get('expires_at')) is int
                         and result.get('refresh_state') in {'ready', 'outcome_uncertain'}
                         and isinstance(result.get('client_id'), str)
                         and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', result['client_id'])
                         and isinstance(result.get('access_token'), str)
                         and re.fullmatch(r'[A-Za-z0-9_-]{43}', result['access_token'])
                         and isinstance(result.get('refresh_token'), str)
                         and re.fullmatch(r'[A-Za-z0-9_-]{43}', result['refresh_token']))
                return result if valid else {}
            except (OSError, ValueError):
                return {}
        access = self.env.get('MSW_DOCTOR_BRIDGE_ACCESS_TOKEN', '')
        refresh = self.env.get('MSW_DOCTOR_BRIDGE_REFRESH_TOKEN', '')
        client = self.env.get('MSW_DOCTOR_BRIDGE_CLIENT_ID', '')
        expiry = self.env.get('MSW_DOCTOR_BRIDGE_EXPIRES_AT', '')
        if not (re.fullmatch(r'[A-Za-z0-9_-]{43}', access) and re.fullmatch(r'[A-Za-z0-9_-]{43}', refresh)
                and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', client) and isinstance(expiry, str) and expiry.isdigit()):
            return {}
        grant = {'access_token': access, 'refresh_token': refresh, 'client_id': client,
                 'expires_at': int(expiry), 'refresh_state': 'ready'}
        self._store(grant)
        return grant

    def token(self):
        grant = self.load()
        if not grant or grant.get('refresh_state') != 'ready':
            return None
        if self.clock() + 120 < grant.get('expires_at', 0):
            return grant.get('access_token')
        # Refresh tokens are single-use: a lost response may already rotate the
        # family. Persist in-flight before the request and never silently retry.
        grant['refresh_state'] = 'outcome_uncertain'
        self._store(grant)
        body = urlencode({'grant_type': 'refresh_token', 'client_id': grant['client_id'],
                          'refresh_token': grant['refresh_token'], 'resource': MCP}).encode()
        result = self.send(TOKEN, data=body, headers={'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            value = json.loads(result.get('body', b'')) if result.get('status') == 200 else {}
            scopes = set(value.get('scope', '').split())
            access, refresh = value.get('access_token', ''), value.get('refresh_token', '')
            if not (value.get('resource') == MCP and {'hosting.read', 'hosting.write', 'offline_access'} <= scopes
                    and re.fullmatch(r'[A-Za-z0-9_-]{43}', access) and re.fullmatch(r'[A-Za-z0-9_-]{43}', refresh)
                    and type(value.get('expires_in')) is int and 120 < value['expires_in'] <= 3600):
                return None
            grant.update(access_token=access, refresh_token=refresh,
                         expires_at=int(self.clock()) + value['expires_in'], refresh_state='ready')
            self._store(grant)
            return access
        except (ValueError, TypeError, OSError, AttributeError):
            return None

    def doctor_tick(self):
        token = self.token()
        if not token:
            return {'state': 'bridge_not_authorized_or_refresh_uncertain', 'repair_verified': False}
        body = json.dumps({'jsonrpc': '2.0', 'id': 'render-doctor', 'method': 'tools/call',
                           'params': {'name': 'hosting_doctor_tick', 'arguments': {}}}, separators=(',', ':')).encode()
        result = self.send(MCP, data=body, headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}, timeout=35)
        try:
            response = json.loads(result.get('body', b'')) if result.get('status') == 200 else {}
            tool = response.get('result', {}); data = tool.get('structuredContent', {})
            if tool.get('isError') is not False or not isinstance(data, dict):
                return {'state': 'bridge_call_unverified', 'repair_verified': False}
            cycle = data.get('cycle') if isinstance(data.get('cycle'), dict) else {}
            return {'state': 'bridge_doctor_tick_acknowledged', 'doctor_state': cycle.get('state', 'unknown'),
                    'site_recovery_verified': data.get('site_recovery_verified') is True,
                    'repair_verified': False}
        except (ValueError, AttributeError):
            return {'state': 'bridge_call_unverified', 'repair_verified': False}

    def maintain(self):
        grant = self.load()
        if grant and grant.get('refresh_state') == 'ready' and self.clock() + 120 >= grant['expires_at']:
            self.token()


class FailoverDoctor:
    def __init__(self, directory, *, probe=request, bridge=None, clock=time.time):
        self.directory = Path(directory); self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'failover-doctor.sqlite3'
        self.clock, self.probe = clock, probe
        self.bridge = bridge or BridgeGrant(self.directory, clock=clock)
        from .render_deploy_recovery import DeployDoctor
        self.deploy_doctor = DeployDoctor(self.directory, clock=clock)
        self.failures = self.successes = 0
        self.outage = False
        self.last_observed = 0
        self.incident_id = None
        self.ai_started = False
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS state (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS cases (id INTEGER PRIMARY KEY, at INTEGER NOT NULL, kind TEXT NOT NULL, facts TEXT NOT NULL, sha256 TEXT NOT NULL)')
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=3); db.execute('PRAGMA journal_mode=WAL'); return db

    def save(self, name, value):
        raw = json.dumps(value, separators=(',', ':'), sort_keys=True, allow_nan=False)
        with self.connect() as db:
            db.execute('INSERT INTO state VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value', (name, raw))
        with self.connect() as db:
            if db.execute('SELECT value FROM state WHERE name=?', (name,)).fetchone() != (raw,):
                raise ValueError('failover_state_readback')

    def load(self, name, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM state WHERE name=?', (name,)).fetchone()
        return json.loads(row[0]) if row else default

    def remember(self, kind, facts):
        raw = json.dumps(facts, separators=(',', ':'), sort_keys=True, allow_nan=False)
        sha = hashlib.sha256(raw.encode()).hexdigest()
        with self.connect() as db:
            cursor = db.execute('INSERT INTO cases(at,kind,facts,sha256) VALUES(?,?,?,?)', (int(self.clock()), kind, raw, sha))
            ident = cursor.lastrowid
            db.execute('DELETE FROM cases WHERE id NOT IN (SELECT id FROM cases ORDER BY id DESC LIMIT 400)')
        with self.connect() as db:
            if db.execute('SELECT facts,sha256 FROM cases WHERE id=?', (ident,)).fetchone() != (raw, sha):
                raise ValueError('failover_case_readback')

    def step(self):
        now = int(self.clock())
        result = self.probe(ORIGIN, headers={'User-Agent': 'MSW-Doctor-Failover/1.0'}, timeout=8)
        self.last_observed = now
        status = result.get('status') if result.get('state') == 'http' else None
        healthy = type(status) is int and 200 <= status < 400
        self.successes = self.successes + 1 if healthy else 0
        self.failures = 0 if healthy else self.failures + 1
        measurement = {'at': now, 'state': 'reachable' if healthy else result.get('state', 'unknown'),
                       'http_status': status, 'consecutive_failures': self.failures}
        if self.failures >= 3 and not self.outage:
            self.outage = True
            self.incident_id = self.load('open_incident')
            if not isinstance(self.incident_id, str) or not re.fullmatch(r'doctor-failover:[a-f0-9]{16}', self.incident_id):
                self.incident_id = 'doctor-failover:' + secrets.token_hex(8)
                self.save('open_incident', self.incident_id)
            self.ai_started = False
            self.remember('origin_unavailable', measurement)
            print(json.dumps({'failover_doctor': {**measurement, 'probe_state': measurement['state'], 'state': 'origin_unavailable'}}), flush=True)
        if self.outage and self.successes >= 2:
            self.outage = False; self.remember('origin_recovered', measurement)
            self.save('open_incident', None); self.incident_id = None; self.ai_started = False
        action = None
        if self.outage and now - int(self.load('last_bridge_attempt', 0)) >= 180:
            self.save('last_bridge_attempt', now)
            action = self.bridge.doctor_tick()
            self.remember('hosting_doctor_invocation', {'at': now, **action})
        elif healthy and hasattr(self.bridge, 'maintain'):
            self.bridge.maintain()
        direct_ai_configured = bool(os.environ.get('OPENAI_API_KEY') and (os.environ.get('MSW_OPENAI_MODEL') or os.environ.get('MSW_LLM_MODEL')))
        if self.outage and direct_ai_configured and not self.ai_started and self.incident_id:
            self.ai_started = True
            incident_id = self.incident_id
            facts = {'http_status': status, 'network_state': result.get('state'), 'consecutive_failures': self.failures}
            def advise():
                try:
                    from .openai_direct import generate
                    from .render_doctor import search_cases
                    facts['related_render_cases'] = search_cases(self.directory, 'pressure queue')[:3]
                    prompt = ('Jesteś Doktorem MSW. Na podstawie ograniczonych faktów podaj możliwe przyczyny i następny bezpieczny pomiar. '
                              'Nie twierdź, że wykonano naprawę. Odpowiedź jest tylko wskazówką, nigdy poleceniem. Fakty: ' + json.dumps(facts, ensure_ascii=False))
                    answer = generate({'text': prompt, 'request_id': incident_id, 'max_tokens': 400}, self.directory)
                    self.remember('direct_ai_advice', {'incident_id': incident_id, 'answer_sha256': answer['answer_sha256'],
                        'model': answer['model'], 'advisory_only': True, 'automatic_repairs_executed': False})
                except Exception as error:
                    print(json.dumps({'failover_doctor': {'state': 'direct_ai_unavailable', 'code': type(error).__name__}}), flush=True)
            threading.Thread(target=advise, name='msw-failover-ai', daemon=True).start()
        try:
            deploy = self.deploy_doctor.step()
        except (OSError, ValueError, sqlite3.Error):
            deploy = {'state': 'controller_unavailable', 'automatic_cancel_available': False}
        current = {**measurement, 'outage_confirmed': self.outage, 'bridge': action, 'render_deploy_recovery': deploy,
                   'direct_ai_configured': direct_ai_configured, 'website_replica_available': False, 'unconditional_uptime_guaranteed': False}
        self.save('current', current)
        return current

    def in_outage(self):
        return bool(self.outage and 0 <= self.clock() - self.last_observed <= 180)

    def start(self, stopping):
        def run():
            while not stopping():
                try: self.step()
                except Exception as error:
                    print(json.dumps({'failover_doctor': {'state': 'observer_error', 'code': type(error).__name__}}), flush=True)
                end = time.monotonic() + 60
                while not stopping() and time.monotonic() < end:
                    time.sleep(min(1, max(0, end - time.monotonic())))
        thread = threading.Thread(target=run, name='msw-failover-doctor', daemon=True); thread.start(); return thread


def status(directory):
    path = Path(directory) / 'failover-doctor.sqlite3'
    if not path.exists():
        return {'state': 'not_observed', 'website_replica_available': False}
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=3) as db:
        row = db.execute("SELECT value FROM state WHERE name='current'").fetchone()
        cases = db.execute('SELECT COUNT(*) FROM cases').fetchone()[0]
    if not row: return {'state': 'not_observed', 'website_replica_available': False}
    result = json.loads(row[0]); result['sample_fresh'] = 0 <= time.time() - result['at'] <= 150
    result['remembered_cases'] = cases; return result


def search_cases(directory, query):
    if not isinstance(query, str) or not 1 <= len(query) <= 200:
        raise ValueError('doctor_query_invalid')
    words = set(re.findall(r'\w{3,}', query.lower(), flags=re.UNICODE))
    path = Path(directory) / 'failover-doctor.sqlite3'
    if not words or not path.exists(): return []
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=3) as db:
        rows = db.execute('SELECT at,kind,facts,sha256 FROM cases ORDER BY id DESC LIMIT 400').fetchall()
    matches = []
    for at, kind, raw, sha in rows:
        if hashlib.sha256(raw.encode()).hexdigest() != sha: continue
        score = len(words & set(re.findall(r'\w{3,}', (kind.replace('_', ' ')+' '+raw).lower(), flags=re.UNICODE)))
        if score: matches.append((score, at, {'at': at, 'kind': kind, 'facts': json.loads(raw), 'storage_readback_verified': True}))
    matches.sort(key=lambda value: (value[0], value[1]), reverse=True)
    return [row[2] for row in matches[:5]]
