"""Single-service Render build recovery. No deploy or arbitrary command execution."""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPSHandler

from .client import NoRedirect

SERVICE = 'srv-dbltdb9srm7s73ec0v00'
BASE = 'https://api.render.com/v1/services/' + SERVICE


def api(method, suffix, key):
    allowed = (method == 'GET' and (suffix == '/deploys?limit=20' or re.fullmatch(r'/deploys/dep-[a-z0-9]+', suffix))) or (
        method == 'POST' and re.fullmatch(r'/deploys/dep-[a-z0-9]+/cancel', suffix))
    if not allowed: return {'ok': False}
    req = Request(BASE + suffix, data=b'' if method == 'POST' else None,
                  headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json'}, method=method)
    try:
        with build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context())).open(req, timeout=10) as response:
            raw = response.read(524289)
            if response.status != 200 or len(raw) > 524288: return {'ok': False}
        body = json.loads(raw)
        return {'ok': isinstance(body, (dict, list)), 'body': body}
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return {'ok': False}


def timestamp(value):
    try:
        if not isinstance(value, str): return None
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return int(parsed.timestamp()) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError): return None


class DeployDoctor:
    def __init__(self, directory, *, send=api, env=None, clock=time.time):
        self.path = Path(directory) / 'render-deploy-doctor.sqlite3'
        self.send, self.env, self.clock = send, os.environ if env is None else env, clock
        with self.connect() as db: db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=3); db.execute('PRAGMA journal_mode=WAL'); return db

    def load(self):
        with self.connect() as db: row = db.execute('SELECT body FROM state WHERE id=1').fetchone()
        return json.loads(row[0]) if row else {}

    def save(self, state):
        raw = json.dumps(state, separators=(',', ':'), sort_keys=True, allow_nan=False)
        with self.connect() as db: db.execute('INSERT INTO state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body', (raw,))
        with self.connect() as db:
            if db.execute('SELECT body FROM state WHERE id=1').fetchone() != (raw,): raise ValueError('render_deploy_state_readback')

    def step(self):
        state = self.load(); now = int(self.clock())
        key = self.env.get('MSW_RENDER_DOCTOR_API_KEY', '')
        valid_key = isinstance(key, str) and 20 <= len(key) <= 512 and not re.search(r'\s', key)
        if self.env.get('MSW_RENDER_DOCTOR_CONTROLLER') != 'render' or not valid_key:
            return {'state': 'controller_not_configured', 'credential_configured': bool(key), 'automatic_cancel_available': False}
        if now - int(state.get('checked_at', 0)) < 300: return state
        state['checked_at'] = now
        # Reconcile uncertain cancellations; never resend them automatically.
        if state.get('action_state') in {'cancel_attempt_started', 'cancel_outcome_unverified'}:
            ident = state.get('action_id', '')
            if not isinstance(ident, str) or not re.fullmatch(r'dep-[a-z0-9]+', ident): return {'state': 'invalid_record'}
            found = self.send('GET', '/deploys/' + ident, key)
            if found.get('ok') and isinstance(found.get('body'), dict) and found['body'].get('id') == ident and found['body'].get('status') == 'canceled':
                state.update(action_state='cancel_verified', cancel_verified_at=now)
            else: state['action_state'] = 'cancel_outcome_unverified'
            self.save(state); return state
        listing = self.send('GET', '/deploys?limit=20', key)
        if not listing.get('ok') or not isinstance(listing.get('body'), list):
            state['state'] = 'api_unavailable'; self.save(state); return state
        deploys = [x.get('deploy') for x in listing['body'] if isinstance(x, dict) and isinstance(x.get('deploy'), dict)]
        active = [d for d in deploys if d.get('status') in {'created', 'queued', 'build_in_progress', 'update_in_progress', 'pre_deploy_in_progress'}]
        if len(active) != 1 or active[0].get('status') != 'build_in_progress' or not any(d.get('status') == 'live' for d in deploys):
            state.update(state='no_single_stalled_build', observations=0, fingerprint=None); self.save(state); return state
        d = active[0]; ident = d.get('id'); start = timestamp(d.get('startedAt')); updated = timestamp(d.get('updatedAt'))
        if not isinstance(ident, str) or not re.fullmatch(r'dep-[a-z0-9]+', ident) or start is None or updated is None or not 0 <= now-start or not 0 <= now-updated or now-start < 10800 or now-updated < 10800:
            state.update(state='build_not_old_enough', observations=0, fingerprint=None); self.save(state); return state
        fingerprint = ident + '|' + d['updatedAt']
        if state.get('fingerprint') != fingerprint:
            state.update(fingerprint=fingerprint, first_seen=now, observations=1)
        else: state['observations'] = int(state.get('observations', 0)) + 1
        state.update(state='stalled_candidate', observed_id=ident); self.save(state)
        if state['observations'] < 3 or now - int(state['first_seen']) < 600 or state.get('action_id') == ident or now - int(state.get('action_at', 0)) < 21600:
            return state
        current = self.send('GET', '/deploys/' + ident, key)
        if not current.get('ok') or not isinstance(current.get('body'), dict): return state
        body = current['body']
        if body.get('id') != ident or body.get('status') != 'build_in_progress' or body.get('updatedAt') != d['updatedAt']: return state
        state.update(action_id=ident, action_at=now, action_state='cancel_attempt_started'); self.save(state)
        self.send('POST', '/deploys/' + ident + '/cancel', key)
        confirm = self.send('GET', '/deploys/' + ident, key)
        verified = (confirm.get('ok') and isinstance(confirm.get('body'), dict) and
                    confirm['body'].get('id') == ident and confirm['body'].get('status') == 'canceled')
        state['action_state'] = 'cancel_verified' if verified else 'cancel_outcome_unverified'
        if verified: state['cancel_verified_at'] = now
        self.save(state); return state
