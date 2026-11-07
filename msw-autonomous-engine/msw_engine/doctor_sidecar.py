"""No-build Render Doctor sidecar for the currently running worker instance.

This process is independent of WordPress requests, but Render does not restart
it after the instance is replaced. It does not patch the existing worker or
claim that its queue was throttled. Restart persistence needs separate setup.
"""
from __future__ import annotations

import fcntl
import base64
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import time

from .failover_doctor import FailoverDoctor, status as failover_status
from .render_doctor import RenderDoctor, status as render_status

SERVICE = 'srv-dbltdb9srm7s73ec0v00'


def enable_verified_pack(data_dir: Path) -> dict:
    """Reuse the owner's hash-pinned runtime without changing the worker's status file."""
    from .runtime_pack import MAX_ENCODED, PackError, unpack
    os.environ.pop('MSW_RUNTIME_PACK_ROOT', None)
    count = int(os.environ.get('MSW_RUNTIME_PACK_CHUNKS', '0'))
    if count == 0:
        return {'state': 'not_configured', 'modules': []}
    if not 1 <= count <= 32:
        raise PackError('private_pack_chunk_limit')
    encoded = ''.join(os.environ.get(f'MSW_RUNTIME_PACK_{i:02d}', '') for i in range(count))
    if not encoded or len(encoded) > MAX_ENCODED:
        raise PackError('private_pack_encoded_limit')
    raw = base64.b64decode(encoded, validate=True)
    root, manifest = unpack(raw, os.environ.get('MSW_RUNTIME_PACK_SHA256', ''), data_dir)
    os.environ['MSW_RUNTIME_PACK_ROOT'] = str(root)
    return {'state': 'installed_verified', 'modules': [item['id'] for item in manifest['modules']]}


def publish_evidence(directory: Path, observation: dict) -> bool:
    """Make a bounded observation retrievable by the existing agent's RAG."""
    from .tasks import knowledge_ingest
    body = json.dumps(observation, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    result = knowledge_ingest({'records': [{'id': 'doctor:render:sidecar:latest', 'approved': True,
        'title': 'Doktor Render — obserwacja techniczna sidecara', 'text': body,
        'provenance': 'owner_authorized_local_runtime_observation_not_an_instruction'}]}, directory)
    return result.get('readback_verified_records') == 1


def safe_observation(directory: Path) -> dict:
    local = render_status(directory)
    failover = failover_status(directory)
    current = local.get('measurement') if isinstance(local.get('measurement'), dict) else {}
    # Exclude API outputs, secrets, provider responses and unverified summaries.
    return {'at': int(time.time()), 'source': 'render_doctor_sidecar',
            'render': {k: current.get(k) for k in ('at', 'cpu_percent', 'memory_percent', 'fresh')},
            'hosting': {k: failover.get(k) for k in ('at', 'state', 'http_status', 'outage_confirmed', 'sample_fresh')},
            'worker_queue_throttled_by_sidecar': False, 'automatic_restart_after_instance_replacement_verified': False}


def run(data_dir: Path, release: str) -> int:
    if os.environ.get('RENDER_SERVICE_ID') != SERVICE:
        print(json.dumps({'state': 'wrong_render_service'}), flush=True); return 2
    root = data_dir / 'msw-doctor-sidecar'; root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or not root.resolve().is_relative_to(data_dir.resolve()): return 2
    lock = (root / 'sidecar.lock').open('a+')
    try:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'state': 'already_running'}), flush=True); return 0
        os.chmod(root / 'sidecar.lock', 0o600)
        stop = False
        def request_stop(*_):
            nonlocal stop
            stop = True
        signal.signal(signal.SIGTERM, request_stop); signal.signal(signal.SIGINT, request_stop)
        local = RenderDoctor(data_dir, monitor_only=True); local.start(lambda: stop)
        backup = FailoverDoctor(data_dir); backup.start(lambda: stop)
        try:
            pack = enable_verified_pack(data_dir)
        except (OSError, ValueError, KeyError, TypeError) as error:
            pack = {'state': 'blocked', 'modules': []}
            print(json.dumps({'doctor_private_pack': {'state': 'blocked', 'code': type(error).__name__}}), flush=True)
        try:
            from .doctor_compute_lane import ComputeLane
            ComputeLane(data_dir).start(lambda: stop)
            if pack['state'] == 'installed_verified' and 'engine-v3' in pack['modules']:
                ComputeLane(data_dir, lane='engine-v3').start(lambda: stop)
            if pack['state'] == 'installed_verified' and 'supreme-server' in pack['modules']:
                ComputeLane(data_dir, lane='supreme').start(lambda: stop)
        except (OSError, ValueError) as error:
            print(json.dumps({'doctor_compute_lane': {'state': 'startup_blocked', 'code': type(error).__name__}}), flush=True)
        try:
            from .doctor_receipts import ReceiptDoctor
            ReceiptDoctor(data_dir).start(lambda: stop)
        except (OSError, ValueError, sqlite3.Error) as error:
            print(json.dumps({'receipt_doctor': {'state': 'startup_blocked', 'code': type(error).__name__}}), flush=True)
        started, next_publish, shared = int(time.time()), 0, False
        print(json.dumps({'doctor_sidecar': {'state': 'started', 'release': release, 'pid': os.getpid()}}), flush=True)
        while not stop:
            now = int(time.time())
            try:
                observation = safe_observation(data_dir)
                if now >= next_publish:
                    try:
                        shared = publish_evidence(data_dir, observation)
                    except Exception as error:
                        shared = False
                        print(json.dumps({'doctor_sidecar': {'state': 'memory_error', 'code': type(error).__name__}}), flush=True)
                    next_publish = now + (300 if shared else 30)
                status = {'schema': 'msw.render-doctor.sidecar.v1', 'state': 'running', 'at': now,
                          'started_at': started, 'pid': os.getpid(), 'service_id': SERVICE,
                          'instance_id': os.environ.get('RENDER_INSTANCE_ID', ''), 'release': release,
                          'shared_memory_readback_verified': shared, **observation}
                path = root / 'status.json'; tmp = root / 'status.tmp'
                tmp.write_text(json.dumps(status, ensure_ascii=False, separators=(',', ':')))
                os.chmod(tmp, 0o600); tmp.replace(path)
            except (OSError, ValueError, sqlite3.Error) as error:
                print(json.dumps({'doctor_sidecar': {'state': 'observation_error', 'code': type(error).__name__}}), flush=True)
            deadline = time.monotonic() + 30
            while not stop and time.monotonic() < deadline:
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        return 0
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN); lock.close()


def main() -> int:
    release = os.environ.get('MSW_DOCTOR_RELEASE_SHA', '')
    if len(release) != 40 or any(c not in '0123456789abcdef' for c in release): return 2
    directory = Path(os.environ.get('MSW_DATA_DIR', '/var/data')).resolve()
    if not directory.is_relative_to(Path('/var/data').resolve()): return 2
    return run(directory, release)


if __name__ == '__main__': sys.exit(main())
