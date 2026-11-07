"""Summarize acknowledged failure receipts without replaying effects.

`updated_at` describes the receipt update/acknowledgement, not necessarily the
time the underlying task failed. No payloads or provider messages are exported.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote


WINDOW_SECONDS = 3600
MAX_ROWS = 1000


def _category(code):
    if not isinstance(code, str):
        return 'other'
    if code.endswith(':mswpc_fleet33_quarantine'):
        return 'satellite_quarantined'
    if code.endswith(':msw_autonomy_source_changed'):
        return 'source_changed'
    if code.endswith(':msw_autonomy_execution_not_registered'):
        return 'execution_unregistered'
    if code == 'native_autonomy_uncertain_no_repeat':
        return 'outcome_uncertain'
    if code.startswith('fed_generate_http_'):
        return 'ai_provider_failure'
    if code.startswith('fed_autonomy_execute_http_50') or code == 'federation_unavailable':
        return 'server_or_transport_failure'
    return 'other'


def summarize(directory, *, now=None):
    now = int(time.time() if now is None else now)
    path = Path(directory) / 'receipts.sqlite3'
    baseline = {'window_seconds': WINDOW_SECONDS, 'time_basis': 'receipt_updated_at_not_execution',
                'automatic_retry_performed': False, 'repair_performed': False}
    if path.is_symlink():
        return {'state': 'unavailable', 'reason': 'unsafe_path', **baseline}
    if not path.is_file():
        return {'state': 'not_found', **baseline}
    try:
        uri = 'file:' + quote(str(path.resolve()), safe='/') + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as db:
            db.execute('PRAGMA query_only=ON')
            rows = db.execute("SELECT CASE WHEN json_valid(result_json) "
                              "THEN json_extract(result_json,'$.error') ELSE NULL END, "
                              "CASE WHEN json_valid(result_json) "
                              "THEN json_extract(result_json,'$.ok') ELSE NULL END, updated_at "
                              "FROM receipts WHERE state='acknowledged' AND updated_at>=? "
                              "ORDER BY updated_at DESC LIMIT ?", (now - WINDOW_SECONDS, MAX_ROWS)).fetchall()
        failures = Counter()
        for error, ok, _ in rows:
            if ok == 0:
                failures[_category(error)] += 1
        return {'state': 'observed', 'receipts_sampled': len(rows), 'failed_in_sample': sum(failures.values()),
                'categories': dict(sorted(failures.items())), 'sample_may_be_truncated': len(rows) == MAX_ROWS,
                **baseline}
    except sqlite3.Error:
        return {'state': 'unavailable', 'reason': 'database_read_failed', **baseline}


class FailureInsights:
    def __init__(self, directory, *, clock=time.time, monotonic=time.monotonic, interval=300):
        self.directory, self.clock, self.monotonic = Path(directory), clock, monotonic
        self.interval, self._last, self._value = interval, None, None

    def snapshot(self):
        current = self.monotonic()
        if self._last is None or current - self._last >= self.interval or current < self._last:
            self._value = summarize(self.directory, now=self.clock())
            self._last = current
        return self._value
