"""Limit WordPress-callback claims when fresh account telemetry is high.

This is not an operating-system CPU limit. It cannot throttle other workers,
WordPress cron, visitor traffic or work which was already claimed.
"""
from __future__ import annotations

import math
import time

from .account_health import read_account_health


LOCAL_KINDS = frozenset(('seo.analyze', 'research.rank', 'knowledge.ingest', 'knowledge.search'))


class HostPressureGuard:
    def __init__(self, client, *, reader=read_account_health, clock=time.monotonic, wall_clock=time.time,
                 sample_interval=60, pressure_claim_interval=60, unknown_policy='keep_running'):
        if unknown_policy not in ('keep_running', 'local_only'):
            raise ValueError('invalid_host_telemetry_policy')
        self.client, self.reader, self.clock = client, reader, clock
        self.wall_clock = wall_clock
        self.unknown_policy, self._restricted = unknown_policy, False
        self.sample_interval, self.pressure_claim_interval = sample_interval, pressure_claim_interval
        self._sample_at, self._last_claim_at = None, None
        self._active, self._recovery_samples = False, 0
        self._observation = {'state': 'not_observed'}

    def observe(self):
        now = self.clock()
        if self._sample_at is not None and 0 <= now - self._sample_at < self.sample_interval:
            at = self._observation.get('measured_at')
            if self._observation.get('state') == 'fresh' and (type(at) is not int or not 0 <= int(self.wall_clock()) - at <= 90):
                self._recovery_samples = 0
                self._observation = {'state': 'unavailable', 'pressure_active': self._active}
            return self._observation
        self._sample_at = now
        try:
            value = self.reader(self.client)
        except Exception:
            value = {'state': 'unavailable'}
        cpu = value.get('cpu_percent') if isinstance(value, dict) else None
        memory = value.get('physical_memory_percent') if isinstance(value, dict) else None
        at = value.get('at') if isinstance(value, dict) else None
        valid = (isinstance(value, dict) and value.get('state') == 'fresh'
                 and type(at) is int and 0 <= int(self.wall_clock()) - at <= 90
                 and type(cpu) in (int, float) and type(memory) in (int, float)
                 and math.isfinite(cpu) and math.isfinite(memory)
                 and 0 <= cpu <= 100 and 0 <= memory <= 100)
        if not valid:
            self._recovery_samples = 0
            self._observation = {'state': 'unavailable', 'pressure_active': self._active}
            return self._observation
        if cpu >= 70 or memory >= 70:
            self._active, self._recovery_samples = True, 0
        elif self._active and cpu < 65 and memory < 65:
            self._recovery_samples += 1
            if self._recovery_samples >= 2:
                self._active, self._recovery_samples = False, 0
        else:
            self._recovery_samples = 0
        self._observation = {'state': 'fresh', 'pressure_active': self._active,
                             'measured_at': at,
                             'cpu_percent': cpu, 'physical_memory_percent': memory,
                             'recovery_samples': self._recovery_samples}
        return self._observation

    def capabilities(self, available):
        observation = self.observe()
        self._restricted = self._active or (self.unknown_policy == 'local_only' and observation.get('state') != 'fresh')
        if self.unknown_policy == 'local_only':
            observation = {**observation, 'restriction_active': self._restricted,
                           'restriction_reason': ('account_pressure' if self._active else
                                                  'account_health_unavailable' if self._restricted else None)}
        if not self._restricted:
            return list(available), observation
        return [kind for kind in available if kind in LOCAL_KINDS], observation

    def claim_allowed(self):
        if not self._restricted or self._last_claim_at is None:
            return True
        elapsed = self.clock() - self._last_claim_at
        return elapsed < 0 or elapsed >= self.pressure_claim_interval

    def mark_claim(self):
        if self._restricted:
            self._last_claim_at = self.clock()
