"""Read bounded operational metadata from the local Hermes runtime.

This is an observer, not a Hermes control channel or proof of business results.
It deliberately excludes message bodies, prompts, provider output and secrets.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import quote


HERMES_HOME = Path('/var/data/msw-hermes/home')
_IDENTIFIER = re.compile(r'^[A-Za-z0-9_.-]{1,80}$')
_PRIVATE = re.compile(r'https?://\S+|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', re.I)
_SECRET = re.compile(r'\b(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+\S+|(?:token|api[_-]?key|password)\s*[:=]\s*\S+)', re.I)


def _safe_text(value, limit=100):
    if not isinstance(value, str):
        return None
    value = ''.join(c if c.isprintable() else ' ' for c in value)
    return _SECRET.sub('[redacted]', _PRIVATE.sub('[redacted]', value))[:limit]


def _cron_file(home: Path, filename: str):
    directory = home / 'cron'
    if directory.is_symlink() or not directory.resolve().is_relative_to(home.resolve()):
        raise OSError('unsafe cron directory')
    return directory / filename


def _gateway(proc_root: Path, release_root: Path):
    """Confirm an actual Hermes gateway process, without returning its argv."""
    try:
        candidates = list(proc_root.iterdir())
    except OSError:
        return {'state': 'unknown'}
    for proc in candidates:
        if not proc.name.isdigit():
            continue
        try:
            if (proc / 'comm').read_text().strip() != 'hermes':
                continue
            cmdline = (proc / 'cmdline').read_bytes()[:16384].split(b'\0')
            cwd = Path(os.readlink(proc / 'cwd')).resolve()
            if (b'hermes_cli.main' in cmdline and b'gateway' in cmdline and b'run' in cmdline
                    and cwd.is_relative_to(release_root.resolve())):
                return {'state': 'running', 'pid': int(proc.name)}
        except (OSError, ValueError):
            continue
    # Permission and proc visibility limitations mean absence is not proof of failure.
    return {'state': 'not_observed'}


def _jobs(home: Path):
    try:
        path = _cron_file(home, 'jobs.json')
        if path.is_symlink() or path.stat().st_size > 262144:
            return {'state': 'unavailable', 'reason': 'unsafe_or_oversized'}
        data = json.loads(path.read_text(encoding='utf-8'))
        jobs = data.get('jobs') if isinstance(data, dict) else data
        if isinstance(jobs, dict):
            jobs = list(jobs.values())
        if not isinstance(jobs, list) or len(jobs) > 1000:
            raise ValueError('unexpected_jobs')
        result = []
        for item in jobs[:20]:
            if not isinstance(item, dict):
                continue
            ident = item.get('id')
            result.append({'id': ident if isinstance(ident, str) and _IDENTIFIER.fullmatch(ident) else None,
                           'name': _safe_text(item.get('name')),
                           'enabled': item.get('enabled') if isinstance(item.get('enabled'), bool) else None,
                           'state': _safe_text(item.get('state'), 32),
                           'last_run_at': _safe_text(item.get('last_run_at'), 40),
                           'next_run_at': _safe_text(item.get('next_run_at'), 40)})
        return {'state': 'observed', 'total': len(jobs),
                'enabled': sum(isinstance(x, dict) and x.get('enabled') is True for x in jobs),
                'items': result, 'truncated': len(jobs) > 20}
    except FileNotFoundError:
        return {'state': 'not_found'}
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return {'state': 'unavailable', 'reason': 'read_failed'}


def _executions(home: Path):
    try:
        path = _cron_file(home, 'executions.db')
    except OSError:
        return {'state': 'unavailable', 'reason': 'unsafe_path'}
    if path.is_symlink():
        return {'state': 'unavailable', 'reason': 'unsafe_path'}
    if not path.is_file():
        return {'state': 'not_found'}
    try:
        uri = 'file:' + quote(str(path), safe='/') + '?mode=ro'
        with sqlite3.connect(uri, uri=True, timeout=1) as db:
            db.execute('PRAGMA query_only=ON')
            rows = db.execute('SELECT job_id,status,started_at,finished_at FROM executions '
                              'ORDER BY started_at DESC LIMIT 8').fetchall()
            incidents = db.execute('SELECT failure_type,state,COUNT(*) FROM cron_incidents '
                                   'GROUP BY failure_type,state LIMIT 20').fetchall()
        return {'state': 'observed',
                'recent': [{'job_id': x[0] if isinstance(x[0], str) and _IDENTIFIER.fullmatch(x[0]) else None,
                            'status': _safe_text(x[1], 32), 'started_at': _safe_text(x[2], 40),
                            'finished_at': _safe_text(x[3], 40)} for x in rows],
                'incidents': [{'type': _safe_text(x[0], 40), 'state': _safe_text(x[1], 32),
                               'count': x[2]} for x in incidents]}
    except sqlite3.Error:
        return {'state': 'unavailable', 'reason': 'database_read_failed'}


def inspect_hermes(home=HERMES_HOME, *, proc_root=Path('/proc'), now=None):
    home = Path(home)
    now = time.time() if now is None else now
    if home.is_symlink() or not home.is_dir():
        return {'schema': 'msw.hermes-observation.v1', 'state': 'not_available',
                'business_result_verified': False}
    try:
        heartbeat = _cron_file(home, 'ticker_heartbeat')
        if heartbeat.is_symlink():
            raise OSError('unsafe heartbeat')
        age = int(now - heartbeat.stat().st_mtime)
        ticker = {'state': 'observed', 'age_seconds': age, 'fresh': -30 <= age <= 180}
    except OSError:
        ticker = {'state': 'not_observed', 'fresh': False}
    return {'schema': 'msw.hermes-observation.v1', 'state': 'observed', 'at': int(now),
            'gateway': _gateway(Path(proc_root), home.parent / 'releases'),
            'scheduler_heartbeat': ticker, 'jobs': _jobs(home), 'executions': _executions(home),
            'business_result_verified': False}
