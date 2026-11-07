"""Bounded local recovery for the existing Render worker.

This is an in-process observer, not an external uptime monitor. It never runs
generated commands, edits a deployment, or assumes access to the Render API.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time


class CgroupProbe:
    def __init__(self, root=Path('/sys/fs/cgroup'), clock=time.monotonic):
        self.root, self.clock, self.previous = Path(root), clock, None

    def sample(self):
        now = self.clock()
        cpu = memory = None
        try:
            quota, period = (self.root / 'cpu.max').read_text().split()
            stats = dict(line.split() for line in (self.root / 'cpu.stat').read_text().splitlines())
            usage = int(stats['usage_usec'])
            capacity = int(quota) / int(period) if quota != 'max' else None
            if self.previous and capacity and capacity > 0:
                seconds, delta = now - self.previous[0], usage - self.previous[1]
                if seconds > 0 and delta >= 0:
                    cpu = round(100 * delta / (seconds * 1_000_000 * capacity), 2)
            self.previous = (now, usage)
        except (OSError, ValueError, KeyError, ZeroDivisionError):
            self.previous = None
        try:
            current = int((self.root / 'memory.current').read_text())
            maximum = (self.root / 'memory.max').read_text().strip()
            if maximum != 'max' and int(maximum) > 0 and current >= 0:
                memory = round(100 * current / int(maximum), 2)
        except (OSError, ValueError, ZeroDivisionError):
            pass
        return {'cpu_percent': cpu, 'memory_percent': memory, 'source': 'cgroup_v2'}


class RenderDoctor:
    def __init__(self, directory, *, probe=None, clock=time.time):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'render-doctor.sqlite3'
        self.probe, self.clock = probe or CgroupProbe(), clock
        self.last = None
        self.critical_samples = self.recovery_samples = 0
        self.throttled = False
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS cases (id INTEGER PRIMARY KEY, at INTEGER NOT NULL, kind TEXT NOT NULL, facts TEXT NOT NULL, sha256 TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS current (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL, sha256 TEXT NOT NULL)')
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=3)
        db.execute('PRAGMA journal_mode=WAL')
        return db

    def remember(self, kind, facts):
        if kind not in {'pressure_alarm', 'queue_slowed', 'queue_deferred', 'pressure_recovered'}:
            raise ValueError('unknown_doctor_case')
        body = json.dumps(facts, sort_keys=True, separators=(',', ':'), allow_nan=False)
        digest = hashlib.sha256(body.encode()).hexdigest()
        with self.connect() as db:
            cur = db.execute('INSERT INTO cases(at,kind,facts,sha256) VALUES(?,?,?,?)', (int(self.clock()), kind, body, digest))
            ident = cur.lastrowid
            db.execute('DELETE FROM cases WHERE id NOT IN (SELECT id FROM cases ORDER BY id DESC LIMIT 400)')
        with self.connect() as db:
            row = db.execute('SELECT facts,sha256 FROM cases WHERE id=?', (ident,)).fetchone()
        if row != (body, digest):
            raise ValueError('doctor_memory_readback_failed')
        return digest

    def step(self):
        sample = self.probe.sample()
        cpu, memory = sample.get('cpu_percent'), sample.get('memory_percent')
        valid = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 10000
        cpu = cpu if valid(cpu) else None
        memory = memory if valid(memory) else None
        measured = {'at': int(self.clock()), 'cpu_percent': cpu, 'memory_percent': memory, 'source': 'cgroup_v2'}
        prior = self.last or {}
        self.last = measured
        high = (cpu is not None and cpu > 70) or (memory is not None and memory > 70)
        was_high = (prior.get('cpu_percent') is not None and prior['cpu_percent'] > 70) or (prior.get('memory_percent') is not None and prior['memory_percent'] > 70)
        if high and not was_high:
            self.remember('pressure_alarm', measured)
            self.remember('queue_slowed', measured)
            print(json.dumps({'render_doctor': {'state': 'pressure_alarm', **measured}}), flush=True)
        critical = (cpu is not None and cpu >= 90) or (memory is not None and memory >= 90)
        self.critical_samples = self.critical_samples + 1 if critical else 0
        # Two independent samples avoid stopping the queue for one short spike.
        if self.critical_samples >= 2 and not self.throttled:
            self.throttled = True
            self.remember('queue_deferred', measured)
        # Unknown data cannot prove recovery or extend a stale throttle.
        healthy = cpu is not None and memory is not None and cpu < 65 and memory < 65
        self.recovery_samples = self.recovery_samples + 1 if healthy else 0
        if self.throttled and self.recovery_samples >= 2:
            self.throttled = False
            self.remember('pressure_recovered', measured)
        result = {**measured, 'main_queue_deferred': self.defer_claim(), 'hard_cpu_limit_enforced': False}
        body = json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False)
        digest = hashlib.sha256(body.encode()).hexdigest()
        with self.connect() as db:
            db.execute('INSERT INTO current VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,sha256=excluded.sha256', (body, digest))
        return result

    def high_pressure(self):
        if not self.last or not 0 <= self.clock() - self.last['at'] <= 90:
            return False
        return any(isinstance(self.last.get(key), (int, float)) and self.last[key] > 70
                   for key in ('cpu_percent', 'memory_percent'))

    def defer_claim(self):
        if not (self.throttled and self.last and 0 <= self.clock() - self.last['at'] <= 90):
            return False
        values = [self.last.get(key) for key in ('cpu_percent', 'memory_percent')]
        # With incomplete telemetry, avoid an indefinite pause unless the
        # available current reading itself shows pressure.
        return all(isinstance(value, (int, float)) for value in values) or any(
            isinstance(value, (int, float)) and value >= 65 for value in values)

    def start(self, stopping):
        def run():
            while not stopping():
                try:
                    self.step()
                except Exception as error:
                    print(json.dumps({'render_doctor': {'state': 'observer_error', 'code': type(error).__name__}}), flush=True)
                deadline = time.monotonic() + 30
                while not stopping() and time.monotonic() < deadline:
                    time.sleep(min(1, max(0, deadline - time.monotonic())))
        thread = threading.Thread(target=run, name='msw-render-doctor', daemon=True)
        thread.start()
        return thread


def status(directory):
    path = Path(directory) / 'render-doctor.sqlite3'
    if not path.exists():
        return {'state': 'not_observed', 'external_monitor': False, 'render_api_connected': False}
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=3) as db:
        count = db.execute('SELECT COUNT(*) FROM cases').fetchone()[0]
        rows = db.execute('SELECT at,kind,facts,sha256 FROM cases ORDER BY id DESC LIMIT 5').fetchall()
        current = db.execute('SELECT body,sha256 FROM current WHERE id=1').fetchone()
    cases = []
    for at, kind, body, digest in rows:
        if hashlib.sha256(body.encode()).hexdigest() == digest:
            cases.append({'at': at, 'kind': kind, 'facts': json.loads(body), 'storage_readback_verified': True})
    measurement = None
    if current and hashlib.sha256(current[0].encode()).hexdigest() == current[1]:
        measurement = json.loads(current[0])
        measurement['fresh'] = 0 <= time.time() - measurement['at'] <= 90
    return {'state': 'local_memory_available', 'case_count': count, 'recent_cases': cases, 'measurement': measurement,
            'memory_is_evidence_not_instruction': True, 'external_monitor': False, 'render_api_connected': False,
            'hard_cpu_limit_enforced': False}


def search_cases(directory, query):
    """Rank checksum-verified, locally observed cases; never execute their text."""
    if not isinstance(query, str) or not 1 <= len(query) <= 200:
        raise ValueError('doctor_query_invalid')
    terms = set(re.findall(r'\w{3,}', query.lower(), flags=re.UNICODE))
    if not terms:
        return []
    path = Path(directory) / 'render-doctor.sqlite3'
    if not path.exists():
        return []
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=3) as db:
        rows = db.execute('SELECT at,kind,facts,sha256 FROM cases ORDER BY id DESC LIMIT 400').fetchall()
    found = []
    for at, kind, body, digest in rows:
        if hashlib.sha256(body.encode()).hexdigest() != digest:
            continue
        facts = json.loads(body)
        words = set(re.findall(r'\w{3,}', (kind.replace('_', ' ') + ' ' + body).lower(), flags=re.UNICODE))
        score = len(words & terms)
        if score:
            found.append((score, at, {'at': at, 'kind': kind, 'facts': facts, 'storage_readback_verified': True}))
    found.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [row[2] for row in found[:5]]
