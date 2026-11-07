"""Slow, leased Render execution of approved local compute jobs.

The existing WordPress queue remains authoritative. Each lane attempts at most
one job per ten minutes and never runs PHP callbacks or publishes content.
Private-runtime lanes defer unless hosting resource measurements are fresh.
Uncertain acknowledgements are retained.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
import time

from .client import Client, TransportError
from .failover_doctor import status as hosting_status
from .render_doctor import status as render_status
from .runner import Worker
from .tasks import handlers


COMPUTE_KINDS = ('seo.analyze', 'research.rank', 'knowledge.ingest', 'knowledge.search')
V3_KINDS = ('engine.v3.ingest',)
SUPREME_KINDS = ('supreme.observe',)
LANE_KINDS = {'seo': COMPUTE_KINDS, 'engine-v3': V3_KINDS, 'supreme': SUPREME_KINDS}
HOST_GATED_LANES = frozenset(('engine-v3', 'supreme'))


class ComputeLane:
    def __init__(self, directory, *, client=None, clock=time.time, worker_factory=Worker,
                 supported_kinds=None, lane='seo', integrity_guard=None):
        if lane not in LANE_KINDS:
            raise ValueError('unknown_compute_lane')
        self.lane = lane
        self.integrity_guard = integrity_guard
        self.allowed_kinds = LANE_KINDS[lane]
        self.directory, self.clock = Path(directory), clock
        self.root = self.directory / 'msw-doctor-sidecar'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.pending = self.root / (lane + '-pending.json')
        self.status_path = self.root / (lane + '-status.json')
        if client is None:
            base = os.environ.get('MSW_WP_URL', '').rstrip('/')
            endpoint = os.environ.get('MSW_TRANSPORT_URL', base + '/wp-content/plugins/msw-unified-engine/transport.php')
            client = Client(base, endpoint, os.environ.get('MSW_NODE', ''), os.environ.get('MSW_SECRET', ''))
        self.client = client
        self.worker = worker_factory(client, self.directory, max_seconds=30, memory_mb=384)
        supported = handlers() if supported_kinds is None else supported_kinds
        self.kinds = tuple(kind for kind in self.allowed_kinds if kind in supported)

    def write(self, path, body):
        if path.is_symlink(): raise ValueError('unsafe_lane_state')
        tmp = path.with_suffix('.tmp')
        if tmp.is_symlink(): raise ValueError('unsafe_lane_temporary')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(body, stream, separators=(',', ':'), sort_keys=True)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)

    def report(self, state, **fields):
        body = {'at': int(self.clock()), 'state': state, 'lane': self.lane, 'kinds': list(self.kinds),
                'minimum_interval_seconds': 600, 'hosting_cpu_measured': False,
                'publication_performed': False, **fields}
        self.write(self.status_path, body)
        print(json.dumps({'doctor_compute_lane': body}, separators=(',', ':')), flush=True)
        return body

    def finish_pending(self):
        ref = json.loads(self.pending.read_text())
        ident, digest = ref.get('id'), ref.get('digest')
        kind = ref.get('kind', 'seo.analyze' if self.lane == 'seo' else None)
        if not (isinstance(ident, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', ident)
                and isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest)
                and kind in self.allowed_kinds):
            raise ValueError('invalid_pending_reference')
        receipt = self.worker.journal.lookup(ident, digest)
        if receipt is None:
            if self.clock() - int(ref.get('created', 0)) < 1800:
                return self.report('waiting_for_unfinished_lease', kind=kind)
            self.pending.unlink()
            return self.report('unfinished_lease_expired_unverified', kind=kind)
        if receipt.get('state') == 'acknowledged':
            self.pending.unlink()
            return self.report('previous_result_acknowledged', kind=kind, server_ack_verified=True)
        try:
            existing = self.client.send('status', {'job_id': ident})
            if (existing.get('state') in {'completed', 'failed'}
                    and existing.get('result_sha256') == receipt['result_sha256']):
                self.worker.journal.mark(ident, 'acknowledged')
                self.pending.unlink()
                return self.report('previous_result_acknowledged', kind=kind, server_ack_verified=True)
            result = self.worker.acknowledge(receipt)
            self.pending.unlink()
            return self.report('previous_result_acknowledged', kind=kind, server_ack_verified=result.get('server_ack_verified') is True)
        except TransportError as error:
            return self.report('result_ack_unverified', kind=kind, code=error.code, http_status=error.status)

    def step(self, stopping=lambda: False):
        if stopping(): return self.report('stopping')
        if self.pending.exists(): return self.finish_pending()
        if self.lane in HOST_GATED_LANES and self.integrity_guard is not None and self.integrity_guard.blocked:
            return self.report('deferred_integrity_blocked')
        if not self.kinds: return self.report('no_supported_compute_kinds')
        site, render = hosting_status(self.directory), render_status(self.directory)
        measurement = render.get('measurement') or {}
        cpu, memory = measurement.get('cpu_percent'), measurement.get('memory_percent')
        if not (site.get('state') == 'reachable' and site.get('sample_fresh') is True
                and measurement.get('fresh') is True and isinstance(cpu, (int, float))
                and isinstance(memory, (int, float)) and cpu < 70 and memory < 70):
            return self.report('deferred_missing_health_or_render_pressure')
        if self.lane in HOST_GATED_LANES:
            from .account_health import read_account_health
            health = read_account_health(self.client, clock=self.clock)
            if health.get('state') != 'fresh':
                return self.report('deferred_host_health_unavailable')
            if health['cpu_percent'] >= 70 or health['physical_memory_percent'] >= 70:
                return self.report('deferred_host_pressure', hosting_cpu_measured=True,
                                   hosting_cpu_percent=health['cpu_percent'],
                                   hosting_memory_percent=health['physical_memory_percent'])
        claim = self.client.send('claim', {'capabilities': list(self.kinds)})
        if claim.get('state') != 'claimed': return self.report('idle')
        job, lease = claim.get('job'), claim.get('lease_token')
        if (not isinstance(job, dict) or job.get('kind') not in self.kinds or not isinstance(lease, str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', str(job.get('id', '')))
                or not re.fullmatch(r'[0-9a-f]{64}', str(job.get('payload_sha256', '')))):
            raise ValueError('unexpected_claim')
        self.write(self.pending, {'id': job['id'], 'digest': job['payload_sha256'],
                                  'kind': job['kind'], 'created': int(self.clock())})
        self.worker.cancel_check = stopping
        receipt = self.worker.execute(job, lease)
        try:
            ack = self.worker.acknowledge(receipt)
        except TransportError as error:
            return self.report('result_ack_unverified', kind=job['kind'], code=error.code, http_status=error.status)
        self.pending.unlink()
        return self.report('completed' if ack.get('state') == 'completed' else 'job_failed_verified',
                           kind=job['kind'], server_ack_verified=ack.get('server_ack_verified') is True,
                           hosting_cpu_measured=self.lane in HOST_GATED_LANES,
                           result_sha256=receipt['result_sha256'])

    def start(self, stopping):
        def run():
            try:
                last = int(json.loads(self.status_path.read_text())['at'])
            except (OSError, ValueError, KeyError, TypeError):
                self.report('scheduled')
                # Stagger new lanes so their first claims do not coincide.
                offset = {'seo': 0, 'engine-v3': 540, 'supreme': 480}[self.lane]
                last = int(self.clock()) - offset
            while not stopping():
                elapsed = max(0, self.clock() - last)
                deadline = time.monotonic() + max(0, 600 - elapsed)
                while not stopping() and time.monotonic() < deadline:
                    time.sleep(min(1, max(0, deadline-time.monotonic())))
                if stopping(): break
                try: self.step(stopping)
                except Exception as error:
                    try: self.report('lane_error', code=type(error).__name__)
                    except OSError: pass
                last = int(self.clock())
        thread = threading.Thread(target=run, name='msw-doctor-' + self.lane + '-lane', daemon=True)
        thread.start()
        return thread
