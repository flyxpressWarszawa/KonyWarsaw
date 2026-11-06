"""Shared outage backoff for WordPress requests across worker subprocesses.

503/504/timeouts open a durable per-origin circuit. After cooldown only one
request tests recovery. This reduces overload; it is not a PHP/hosting repair.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit


class OriginPaused(URLError):
    def __init__(self, seconds):
        self.retry_after = max(1, int(seconds))
        super().__init__('origin_recovery_backoff')


class RecoveryCircuit:
    def __init__(self, directory, clock=time.time):
        self.path = Path(directory) / 'transport-recovery.sqlite3'
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS origins (origin TEXT PRIMARY KEY,failures INTEGER NOT NULL,window_at REAL NOT NULL,open_until REAL NOT NULL,probe_until REAL NOT NULL,level INTEGER NOT NULL)')
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        return db

    def enter(self, origin, timeout):
        now = self.clock()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM origins WHERE origin=?', (origin,)).fetchone()
            if row is None:
                db.execute('INSERT INTO origins VALUES(?,0,?,0,0,0)', (origin, now))
                return False
            if row['open_until'] > now or row['probe_until'] > now:
                raise OriginPaused(max(row['open_until'], row['probe_until']) - now)
            probe = row['failures'] >= 3
            if probe:
                db.execute('UPDATE origins SET probe_until=? WHERE origin=?', (now + timeout + 15, origin))
            return probe

    def success(self, origin, probe):
        with self.connect() as db:
            if probe:
                db.execute('UPDATE origins SET failures=0,window_at=?,open_until=0,probe_until=0,level=0 WHERE origin=?', (self.clock(), origin))
                print(json.dumps({'transport_recovery': {'state': 'probe_succeeded', 'origin': origin}}), flush=True)

    def failure(self, origin, *, retry_after=0):
        now = self.clock()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM origins WHERE origin=?', (origin,)).fetchone()
            failures = (row['failures'] if row['failures'] >= 3 or now - row['window_at'] < 60 else 0) + 1
            opened = failures >= 3
            level = min(5, row['level'] + 1) if opened else 0
            delay = max(min(900, retry_after), min(900, 30 * 2 ** level)) if opened else 0
            db.execute('UPDATE origins SET failures=?,window_at=?,open_until=?,probe_until=0,level=? WHERE origin=?',
                       (failures, now, now + delay if opened else 0, level, origin))
        if opened:
            print(json.dumps({'transport_recovery': {'state': 'cooldown', 'origin': origin, 'retry_in_seconds': delay}}), flush=True)


@contextmanager
def protected_open(opener, request, *, timeout):
    directory = os.environ.get('MSW_DATA_DIR')
    if not directory:
        with opener.open(request, timeout=timeout) as response:
            yield response
        return
    parsed = urlsplit(request.full_url)
    origin = parsed.scheme + '://' + (parsed.hostname or '')
    circuit = RecoveryCircuit(directory)
    probe = circuit.enter(origin, timeout)
    try:
        with opener.open(request, timeout=timeout) as response:
            yield response
    except HTTPError as error:
        if error.code in {429, 502, 503, 504}:
            retry = error.headers.get('Retry-After', '') if error.headers else ''
            circuit.failure(origin, retry_after=int(retry) if retry.isdigit() and len(retry) < 7 else 0)
        else:
            circuit.success(origin, probe)
        raise
    except (URLError, TimeoutError, ConnectionError):
        circuit.failure(origin)
        raise
    else:
        circuit.success(origin, probe)


def status(directory):
    path = Path(directory) / 'transport-recovery.sqlite3'
    if not path.exists():
        return {'state': 'not_observed', 'origins_in_cooldown': []}
    with sqlite3.connect(path, timeout=10) as db:
        rows = db.execute('SELECT origin,open_until,probe_until FROM origins WHERE failures>=3').fetchall()
    now = time.time()
    return {'state': 'active', 'origins_in_cooldown': [
        {'origin': row[0], 'retry_in_seconds': max(0, int(max(row[1:]) - now))} for row in rows]}
