"""Small, bounded incident classifier operating on Render observations.

Only technical flags are stored. A signed transport ACK is not itself evidence
of a successful repair: training requires a separately structured, positive
repair-feedback receipt. The classifier proposes no commands and changes no
service. It is a tiny statistical classifier, not a language model.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import quote


LABELS = ('resource_pressure', 'server_error', 'authentication', 'missing_route',
          'rate_limit', 'bridge_failure', 'integrity_failure', 'other')
FEATURES = ('render_cpu_70', 'render_cpu_90', 'render_memory_70', 'render_memory_90',
            'hosting_outage', 'http_400', 'http_401', 'http_403', 'http_404',
            'http_408', 'http_429', 'http_other_4xx', 'http_500', 'http_502',
            'http_503', 'http_504', 'http_other_5xx', 'queue_failure',
            'provider_failure', 'transport_failure', 'integrity_blocked')
MAX_EXAMPLES = 10000


def _readonly(path: Path):
    if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
        return None
    uri = 'file:' + quote(str(path.resolve()), safe='/') + '?mode=ro'
    db = sqlite3.connect(uri, uri=True, timeout=2)
    db.execute('PRAGMA query_only=ON')
    return db


def signals(observation: dict) -> tuple[str, ...]:
    """Convert fresh, already sanitized observations to fixed vocabulary flags."""
    if not isinstance(observation, dict):
        raise ValueError('doctor_observation_invalid')
    flags = set()
    render = observation.get('render')
    if isinstance(render, dict) and render.get('fresh') is True:
        for name, field in (('cpu', 'cpu_percent'), ('memory', 'memory_percent')):
            value = render.get(field)
            if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100:
                for threshold in (70, 90):
                    if value >= threshold:
                        flags.add(f'render_{name}_{threshold}')
    hosting = observation.get('hosting')
    if isinstance(hosting, dict) and hosting.get('sample_fresh') is True:
        if hosting.get('outage_confirmed') is True:
            flags.add('hosting_outage')
        code = hosting.get('http_status')
        if type(code) is int:
            if code in (400, 401, 403, 404, 408, 429, 500, 502, 503, 504):
                flags.add(f'http_{code}')
            elif 400 <= code <= 499:
                flags.add('http_other_4xx')
            elif 500 <= code <= 599:
                flags.add('http_other_5xx')
    failures = observation.get('acknowledged_failure_receipts')
    if isinstance(failures, dict) and failures.get('state') == 'observed':
        count = failures.get('failed_in_sample')
        if type(count) is int and count > 0:
            flags.add('queue_failure')
        categories = failures.get('categories')
        if isinstance(categories, dict):
            if type(categories.get('ai_provider_failure')) is int and categories['ai_provider_failure'] > 0:
                flags.add('provider_failure')
            if type(categories.get('server_or_transport_failure')) is int and categories['server_or_transport_failure'] > 0:
                flags.add('transport_failure')
    integrity = observation.get('private_pack_integrity')
    if isinstance(integrity, dict) and integrity.get('state') == 'integrity_blocked':
        flags.add('integrity_blocked')
    return tuple(sorted(flags))


def _model_path(directory: Path) -> Path:
    return Path(directory) / 'doctor-learning' / 'incidents.sqlite3'


def _store(directory: Path, job_id: str, label: str, flags: tuple[str, ...], result_hash: str) -> str:
    path = _model_path(directory)
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError('doctor_learning_unsafe_path')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    body = json.dumps(list(flags), separators=(',', ':'))
    with closing(sqlite3.connect(path, timeout=2)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS examples (job_id TEXT PRIMARY KEY, label TEXT NOT NULL, flags TEXT NOT NULL, result_sha256 TEXT NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute('SELECT label, flags, result_sha256 FROM examples WHERE job_id=?', (job_id,)).fetchone()
        if prior:
            if prior != (label, body, result_hash):
                raise ValueError('doctor_learning_receipt_conflict')
            return 'already_learned'
        if db.execute('SELECT COUNT(*) FROM examples').fetchone()[0] >= MAX_EXAMPLES:
            raise ValueError('doctor_learning_capacity')
        db.execute('INSERT INTO examples VALUES(?,?,?,?)', (job_id, label, body, result_hash))
        db.commit()
        if db.execute('SELECT label, flags, result_sha256 FROM examples WHERE job_id=?', (job_id,)).fetchone() != (label, body, result_hash):
            raise ValueError('doctor_learning_readback_failed')
    os.chmod(path, 0o600)
    return 'learned'


def learn_recent(directory: Path, *, now=None, max_receipts=50) -> dict:
    """Learn from bounded, acknowledged, checksum-verified repair feedback.

    The source asserts that an effect was verified; this function verifies the
    receipt and its checksum, but cannot independently prove cause or effect.
    Other ACKs, including ordinary successful jobs, are not training samples.
    """
    if type(max_receipts) is not int or not 1 <= max_receipts <= 100:
        raise ValueError('doctor_learning_scan_limit')
    db = _readonly(Path(directory) / 'receipts.sqlite3')
    if db is None:
        return {'state': 'journal_unavailable', 'learned': 0}
    now = int(time.time() if now is None else now)
    with closing(db):
        rows = db.execute("SELECT job_id,result_json,result_sha256 FROM receipts WHERE state='acknowledged' AND updated_at>=? ORDER BY updated_at DESC LIMIT ?", (now - 86400, max_receipts)).fetchall()
    learned = skipped = 0
    for job_id, raw, digest in rows:
        if (not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', job_id)
                or not isinstance(raw, str) or len(raw.encode()) > 524288
                or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or hashlib.sha256(raw.encode()).hexdigest() != digest):
            skipped += 1
            continue
        try:
            result = json.loads(raw)
            data = result.get('data') if isinstance(result, dict) and result.get('ok') is True else None
            if not isinstance(data, dict) or data.get('schema') != 'msw.doctor.repair_feedback.v1' or data.get('effect_verified') is not True:
                skipped += 1
                continue
            label = data.get('cause')
            evidence = data.get('evidence_sha256')
            if label not in LABELS or not isinstance(evidence, str) or not re.fullmatch(r'[0-9a-f]{64}', evidence):
                skipped += 1
                continue
            flags = signals(data.get('observation_before'))
            if not flags:
                skipped += 1
                continue
            learned += _store(Path(directory), job_id, label, flags, digest) == 'learned'
        except (json.JSONDecodeError, TypeError, ValueError):
            skipped += 1
    return {'state': 'scanned', 'receipts_sampled': len(rows), 'learned': learned,
            'skipped': skipped, 'source_effect': 'asserted_by_receipt_not_independently_proven',
            'customer_content_stored': False}


def diagnose(directory: Path, observation: dict) -> dict:
    """Rank fixed labels with a tiny Bernoulli Naive Bayes model, or abstain."""
    present = signals(observation)
    base = {'signals': list(present), 'model': 'bernoulli_naive_bayes',
            'language_model': False, 'repair_performed': False,
            'source_effect': 'asserted_by_receipt_not_independently_proven'}
    db = _readonly(_model_path(directory))
    if db is None:
        return {**base, 'state': 'awaiting_repair_feedback', 'training_examples': 0, 'prediction': None}
    with closing(db):
        rows = db.execute('SELECT label,flags FROM examples ORDER BY job_id LIMIT ?', (MAX_EXAMPLES,)).fetchall()
    samples = []
    for label, raw in rows:
        try:
            flags = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if label in LABELS and isinstance(flags, list) and all(isinstance(f, str) and f in FEATURES for f in flags):
            samples.append((label, set(flags)))
    active = {label for label, _ in samples}
    if len(samples) < 6 or len(active) < 2 or not present:
        return {**base, 'state': 'insufficient_evidence', 'training_examples': len(samples), 'prediction': None}
    scores = {}
    for label in sorted(active):
        group = [flags for tag, flags in samples if tag == label]
        score = math.log((len(group) + 1) / (len(samples) + len(active)))
        for feature in FEATURES:
            likelihood = (sum(feature in flags for flags in group) + 1) / (len(group) + 2)
            score += math.log(likelihood if feature in present else 1 - likelihood)
        scores[label] = score
    shift = max(scores.values())
    weights = {label: math.exp(score - shift) for label, score in scores.items()}
    label = max(weights, key=weights.get)
    score = weights[label] / sum(weights.values())
    return {**base, 'state': 'hypothesis' if score >= 0.6 else 'uncertain',
            'training_examples': len(samples), 'prediction': label if score >= 0.6 else None,
            'relative_score': round(score, 4), 'score_calibrated': False,
            'causality_proven': False}
