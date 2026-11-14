import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from msw_engine.wp_registry_cycle import (SyncError, SCHEMA, SOURCE_SCHEMA,
    build_payload, canonical, digest, strict_json, timestamp, verify_snapshot,
    sync_once, request_json, read_source, run_live_cycle, SERVICE, SOURCE_URL)

NOW = datetime(2026, 11, 14, 21, 30, 0, tzinfo=timezone.utc)
STAMP = '2026-11-14T21:30:00Z'


def fixture():
    return {'schema': SOURCE_SCHEMA, 'safety': {'paidDailyLimitUsd': 0.3},
        'evidence': {'registry': {'revision': 119, 'lastAckRevision': 119, 'total': 1},
            'hermes': {'status': 'CONNECTED', 'routerVersion': '2.1.7', 'lastSeen': STAMP,
                       'todayPaidSpentUsd': 0.28, 'todayReservedUsd': 0.008}},
        'providerRoster': [{'id': 'openai', 'group': 'msw30', 'kind': 'provider',
                           'registryStatus': 'REGISTERED', 'hold': False, 'keyReported': True}],
        'runtimeRoutes': [{'id': 'openai-gpt5-mini', 'provider': 'openai',
            'model': 'gpt-5-mini', 'tier': 'paid', 'status': 'READY',
            'reason': 'LIVE_RESPONSE', 'lastTest': STAMP}]}


class Remote:
    def __init__(self):
        self.posts = 0
        self.reads = 0
        self.value = None
        self.raise_after_write = False
        self.raise_before_write = False
    def post(self, payload):
        self.posts += 1
        if self.raise_before_write:
            raise SyncError('transport_unknown')
        self.value = copy.deepcopy(payload)
        self.value['digestSha256'] = 'f' * 64
        if self.raise_after_write:
            raise SyncError('transport_unknown')
        return {'stored': True, 'registryRevision': payload['registryRevision'],
                'lastAckRevision': payload['lastAckRevision'], 'digestSha256': 'f' * 64}
    def get(self):
        self.reads += 1
        return {'snapshotPresent': self.value is not None, 'snapshot': copy.deepcopy(self.value)}


class RegistryCycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'journal'
        self.remote = Remote()
    def tearDown(self):
        self.temp.cleanup()
    def run_sync(self, doc=None, now=NOW):
        return sync_once(doc or fixture(), self.root, self.remote.post, self.remote.get, now)
    def test_payload_valid(self):
        p = build_payload(fixture(), NOW)
        self.assertEqual(p['schema'], SCHEMA)
        self.assertEqual(p['registryRevision'], 119)
    def test_observation_time_not_refreshed(self):
        p = build_payload(fixture(), NOW + timedelta(minutes=10))
        self.assertEqual(p['observedAtUtc'], STAMP)
    def test_budget_from_correct_root(self):
        d = fixture(); d['safety']['paidDailyLimitUsd'] = 0.17
        d['evidence']['safety'] = {'paidDailyLimitUsd': 999}
        self.assertEqual(build_payload(d, NOW)['hermes']['paidDailyLimitUsd'], 0.17)
    def test_no_default_for_missing_budget(self):
        d = fixture(); d['safety'] = {}
        with self.assertRaisesRegex(SyncError, 'budget_contract'): build_payload(d, NOW)
    def test_bool_revision_rejected(self):
        for key in ['revision', 'lastAckRevision']:
            d = fixture(); d['evidence']['registry'][key] = True
            with self.assertRaises(SyncError): build_payload(d, NOW)
    def test_negative_revision_rejected(self):
        d = fixture(); d['evidence']['registry']['revision'] = -1
        with self.assertRaises(SyncError): build_payload(d, NOW)
    def test_ack_above_revision(self):
        d = fixture(); d['evidence']['registry']['lastAckRevision'] = 120
        with self.assertRaises(SyncError): build_payload(d, NOW)
    def test_invalid_budget_values(self):
        for val in [True, '0.3', -1, 1001, float('inf'), float('nan')]:
            d = fixture(); d['safety']['paidDailyLimitUsd'] = val
            with self.subTest(val=val), self.assertRaises(SyncError): build_payload(d, NOW)
    def test_stale_source(self):
        with self.assertRaisesRegex(SyncError, 'stale_source'):
            build_payload(fixture(), NOW + timedelta(seconds=1801))
    def test_future_source(self):
        with self.assertRaisesRegex(SyncError, 'stale_source'):
            build_payload(fixture(), NOW - timedelta(seconds=301))
    def test_timezone_required(self):
        for value in ['2026-11-14', '2026-11-14T21:30:00', '2026-11-14T21:30:00+02:00', None, 42]:
            with self.subTest(value=value), self.assertRaises(SyncError): timestamp(value)
    def test_disconnected_source(self):
        d = fixture(); d['evidence']['hermes']['status'] = 'OFFLINE'
        with self.assertRaisesRegex(SyncError, 'source_not_connected'): build_payload(d, NOW)
    def test_duplicate_json_keys(self):
        with self.assertRaisesRegex(SyncError, 'duplicate_json_key'): strict_json(b'{"a":1,"a":2}')
    def test_nonfinite_json(self):
        for value in [b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}']:
            with self.assertRaises(SyncError): strict_json(value)
    def test_invalid_utf8(self):
        with self.assertRaises(SyncError): strict_json(b'{"x":"\xff"}')
    def test_invalid_source_structure(self):
        for doc in [[], {}, {'schema': SOURCE_SCHEMA}, dict(fixture(), evidence=[])]:
            with self.subTest(doc=doc), self.assertRaises(SyncError): build_payload(doc, NOW)
    def test_provider_limit(self):
        d = fixture(); d['providerRoster'] *= 201
        with self.assertRaisesRegex(SyncError, 'record_bounds'): build_payload(d, NOW)
    def test_route_limit(self):
        d = fixture(); d['runtimeRoutes'] *= 81
        with self.assertRaisesRegex(SyncError, 'record_bounds'): build_payload(d, NOW)
    def test_count_mismatch(self):
        d = fixture(); d['evidence']['registry']['total'] = 2
        with self.assertRaisesRegex(SyncError, 'registry_count_mismatch'): build_payload(d, NOW)
    def test_duplicate_provider(self):
        d = fixture(); d['providerRoster'] *= 2; d['evidence']['registry']['total'] = 2
        with self.assertRaisesRegex(SyncError, 'duplicate_provider_id'): build_payload(d, NOW)
    def test_duplicate_route(self):
        d = fixture(); d['runtimeRoutes'] *= 2
        with self.assertRaisesRegex(SyncError, 'duplicate_route_id'): build_payload(d, NOW)
    def test_boolean_fields_strict(self):
        d = fixture(); d['providerRoster'][0]['hold'] = 'false'
        with self.assertRaisesRegex(SyncError, 'boolean_contract'): build_payload(d, NOW)
    def test_invalid_id_no_silent_rewrite(self):
        d = fixture(); d['providerRoster'][0]['id'] = '../OpenAI'
        with self.assertRaisesRegex(SyncError, 'text_contract'): build_payload(d, NOW)
    def test_model_length(self):
        d = fixture(); d['runtimeRoutes'][0]['model'] = 'x' * 101
        with self.assertRaises(SyncError): build_payload(d, NOW)
    def test_secret_fields_not_projected(self):
        d = fixture(); d['providerRoster'][0]['api_key'] = 'NEVER_LOG'
        d['runtimeRoutes'][0]['prompt'] = 'PRIVATE_PROMPT'
        p = canonical(build_payload(d, NOW))
        self.assertNotIn(b'NEVER_LOG', p); self.assertNotIn(b'PRIVATE_PROMPT', p)
    def test_full_readback_positive(self):
        r = self.run_sync()
        self.assertEqual(r['state'], 'verified_metadata_cycle')
        self.assertFalse(r['inferenceTransportVerified'])
        self.assertEqual(r['contentSha256'], digest(build_payload(fixture(), NOW)))
        self.assertEqual(self.remote.posts, 1)
    def test_server_digest_alone_insufficient(self):
        p = build_payload(fixture(), NOW)
        self.remote.post(p); self.remote.value['routes'][0]['status'] = 'ERROR'
        with self.assertRaisesRegex(SyncError, 'independent_content_mismatch'):
            verify_snapshot(p, self.remote.get())
    def test_ack_readback_mismatch(self):
        p = build_payload(fixture(), NOW); self.remote.post(p)
        self.remote.value['lastAckRevision'] = 118
        with self.assertRaises(SyncError): verify_snapshot(p, self.remote.get())
    def test_invalid_server_digest(self):
        p = build_payload(fixture(), NOW); self.remote.post(p)
        self.remote.value['digestSha256'] = 'z' * 64
        with self.assertRaisesRegex(SyncError, 'server_digest_invalid'): verify_snapshot(p, self.remote.get())
    def test_unknown_write_reconciled_no_post(self):
        self.remote.raise_after_write = True
        with self.assertRaises(SyncError): self.run_sync()
        self.remote.raise_after_write = False
        r = self.run_sync()
        self.assertEqual(r['state'], 'reconciled_without_post')
        self.assertEqual(self.remote.posts, 1)
    def test_stale_source_reconciles_earlier_applied_write(self):
        self.remote.raise_after_write = True
        with self.assertRaisesRegex(SyncError, 'transport_unknown'):
            self.run_sync()
        self.remote.raise_after_write = False
        # Heartbeat is now stale but a previously sent POST can still be
        # reconciled via GET without a second provider or WordPress call.
        with self.assertRaisesRegex(SyncError, 'stale_source'):
            self.run_sync(now=NOW + timedelta(minutes=31))
        with sqlite3.connect(self.root / 'sync.sqlite3') as db:
            row = db.execute("SELECT state,receipt FROM syncs").fetchone()
        self.assertEqual(row[0], 'verified')
        self.assertIn('contentSha256', row[1])
        self.assertEqual(self.remote.posts, 1)
        self.assertGreaterEqual(self.remote.reads, 1)

    def test_stale_source_with_unknown_write_never_resends(self):
        self.remote.raise_before_write = True
        with self.assertRaisesRegex(SyncError, 'transport_unknown'):
            self.run_sync()
        self.remote.raise_before_write = False
        with self.assertRaisesRegex(SyncError, 'snapshot_missing'):
            self.run_sync(now=NOW + timedelta(minutes=31))
        with sqlite3.connect(self.root / 'sync.sqlite3') as db:
            self.assertEqual(db.execute("SELECT state FROM syncs").fetchone()[0], 'pending')
        self.assertEqual(self.remote.posts, 1)

    def test_unknown_without_remote_does_not_repeat(self):
        self.remote.raise_before_write = True
        with self.assertRaises(SyncError): self.run_sync()
        self.remote.raise_before_write = False
        with self.assertRaisesRegex(SyncError, 'snapshot_missing'): self.run_sync()
        self.assertEqual(self.remote.posts, 1)
    def test_duplicate_after_process_restart_no_post(self):
        self.run_sync()
        r = self.run_sync(now=NOW + timedelta(minutes=1))
        self.assertEqual(r['state'], 'unchanged_verified')
        self.assertEqual(self.remote.posts, 1)
    def test_two_distinct_source_cycles(self):
        self.run_sync()
        d = fixture(); d['evidence']['hermes']['lastSeen'] = '2026-11-14T21:31:00Z'
        self.run_sync(d, NOW + timedelta(minutes=1))
        self.assertEqual(self.remote.posts, 2)
    def test_source_regression_no_post(self):
        self.run_sync(); d = fixture(); d['evidence']['registry']['revision'] = 118
        d['evidence']['registry']['lastAckRevision'] = 118
        with self.assertRaisesRegex(SyncError, 'source_regression'): self.run_sync(d)
        self.assertEqual(self.remote.posts, 1)
    def test_unresolved_previous_blocks_new_source(self):
        self.remote.raise_before_write = True
        with self.assertRaises(SyncError): self.run_sync()
        self.remote.raise_before_write = False
        d = fixture(); d['evidence']['hermes']['lastSeen'] = '2026-11-14T21:31:00Z'
        with self.assertRaises(SyncError): self.run_sync(d, NOW + timedelta(minutes=1))
        self.assertEqual(self.remote.posts, 1)
    def test_symbolic_storage_rejected(self):
        other = Path(self.temp.name) / 'other'; other.mkdir(); self.root.symlink_to(other)
        with self.assertRaisesRegex(SyncError, 'storage_symlink'): self.run_sync()
        self.assertEqual(self.remote.posts, 0)
    def test_symbolic_database_rejected(self):
        self.root.mkdir(); other = Path(self.temp.name) / 'other.db'; other.write_text('')
        (self.root / 'sync.sqlite3').symlink_to(other)
        with self.assertRaisesRegex(SyncError, 'storage_symlink'): self.run_sync()
    def test_journal_permissions(self):
        self.run_sync()
        self.assertEqual((self.root.stat().st_mode & 0o777), 0o700)
        self.assertEqual(((self.root / 'sync.sqlite3').stat().st_mode & 0o777), 0o600)
    def test_no_unapproved_host(self):
        with self.assertRaisesRegex(SyncError, 'destination_rejected'):
            request_json('https://example.com', 'POST', {}, {})
    def test_blob_integrity(self):
        raw = canonical(fixture()); meta = {'type': 'file', 'content': base64.b64encode(raw).decode(), 'sha': 'x'*40}
        with patch('msw_engine.wp_registry_cycle.request_json', return_value=meta):
            with self.assertRaisesRegex(SyncError, 'github_blob_integrity'):
                read_source({'MSW_HERMES_GITHUB_TOKEN': 'test-token'})
    def test_blob_valid(self):
        raw = canonical(fixture()); meta = {'type': 'file', 'content': base64.b64encode(raw).decode(),
            'sha': hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()}
        with patch('msw_engine.wp_registry_cycle.request_json', return_value=meta):
            self.assertEqual(read_source({'MSW_HERMES_GITHUB_TOKEN': 'test-token'}), fixture())
    def test_wrong_service_blocked(self):
        with patch.dict(os.environ, {'MSW_HERMES_WP_REGISTRY_CYCLE_ENABLED': '1', 'RENDER_SERVICE_ID':'production'}, clear=True):
            self.assertEqual(run_live_cycle()['state'], 'wrong_service_blocked')
    def test_default_disabled(self):
        with patch.dict(os.environ, {}, clear=True): self.assertEqual(run_live_cycle()['state'], 'disabled')
    def test_missing_persistent_mount(self):
        with patch.dict(os.environ, {'MSW_HERMES_WP_REGISTRY_CYCLE_ENABLED':'1','RENDER_SERVICE_ID':SERVICE}, clear=True), patch('os.path.ismount', return_value=False):
            self.assertEqual(run_live_cycle()['state'], 'persistent_disk_not_verified')
    def test_float_and_integer_canonical_equivalence(self):
        self.assertEqual(digest({'a':0.0,'b':1.0}), digest({'a':0,'b':1}))
    def test_false_not_same_as_zero(self):
        self.assertNotEqual(digest({'a':False}), digest({'a':0}))
    def test_new_remote_state_not_overwritten_by_old(self):
        self.run_sync(); self.remote.value['registryRevision'] = 120
        with self.assertRaises(SyncError): self.run_sync()
        self.assertEqual(self.remote.posts, 1)
    def test_journal_retention_bounded(self):
        for i in range(131):
            t = NOW + timedelta(seconds=i)
            d = fixture(); d['evidence']['hermes']['lastSeen'] = t.isoformat().replace('+00:00','Z')
            self.run_sync(d, t)
        with sqlite3.connect(self.root/'sync.sqlite3') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM syncs').fetchone()[0], 128)

if __name__ == '__main__': unittest.main()
