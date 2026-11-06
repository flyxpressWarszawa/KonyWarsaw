"""Discover all active WP modules; reuse the signed, leased native executor."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from .client import encode


def connect(directory):
    root = directory / 'autonomous-agent'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(root / 'wordpress-tools.sqlite3', timeout=10)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS inventory (id TEXT PRIMARY KEY,body TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS native_work (job_id TEXT PRIMARY KEY,receipt_sha256 TEXT NOT NULL,modules TEXT NOT NULL,hook TEXT NOT NULL,at INTEGER NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS native_work_time ON native_work(at)')
    db.execute('CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY,value TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS satellites (id TEXT PRIMARY KEY,url TEXT NOT NULL)')
    if 'site_id' not in {r[1] for r in db.execute('PRAGMA table_info(native_work)')}:
        try:
            db.execute("ALTER TABLE native_work ADD COLUMN site_id TEXT NOT NULL DEFAULT 'central'")
        except sqlite3.OperationalError:
            if 'site_id' not in {r[1] for r in db.execute('PRAGMA table_info(native_work)')}:
                raise
    return db


def record_native_receipt(directory, receipt):
    answer = json.loads(receipt['result_json'])
    data = answer.get('data', {})
    if (answer.get('ok') is not True or not isinstance(data, dict)
            or data.get('schema') != 'msw.native-autonomy.receipt.v1'
            or data.get('native_callbacks_executed') is not True or data.get('storage_readback_verified') is not True):
        return
    modules, hook = data.get('modules'), data.get('hook')
    if not isinstance(modules, list) or not modules or not all(isinstance(m, str) and len(m) < 201 for m in modules) or not isinstance(hook, str) or len(hook) > 200:
        return
    site = data.get('site_id', 'central')
    if site != 'central' and (not isinstance(site, str) or not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', site)
                              or data.get('satellite_hmac_readback_verified') is not True):
        return
    row = (receipt['job_id'], receipt['result_sha256'], json.dumps(modules), hook, int(time.time()))
    with connect(directory) as db:
        db.execute('INSERT OR IGNORE INTO native_work(job_id,receipt_sha256,modules,hook,at,site_id) VALUES(?,?,?,?,?,?)', (*row, site))
        db.execute('DELETE FROM native_work WHERE job_id NOT IN (SELECT job_id FROM native_work ORDER BY at DESC LIMIT 5000)')
    with connect(directory) as db:
        stored = db.execute('SELECT receipt_sha256,modules,hook FROM native_work WHERE job_id=?', (row[0],)).fetchone()
    if stored != row[1:4]:
        raise ValueError('agent_native_receipt_conflict')


def summary(directory):
    path = directory / 'autonomous-agent' / 'wordpress-tools.sqlite3'
    if not path.exists():
        return {'state': 'not_observed', 'scope': 'active_plugins_only'}
    with connect(directory) as db:
        rows = [json.loads(r[0]) for r in db.execute('SELECT body FROM inventory ORDER BY id')]
        completed = db.execute('SELECT modules,site_id FROM native_work').fetchall()
        satellites = {r[0] for r in db.execute('SELECT id FROM satellites')}
    used = {m for row in completed for m in json.loads(row[0])}
    return {'state': 'catalogued' if rows else 'awaiting_catalogue', 'scope': 'active_plugins_only',
        'active_modules': len(rows), 'modules_with_native_read_tools': sum(bool(r.get('native_probe')) for r in rows),
        'native_jobs_completed_with_ack': len(completed), 'modules_with_native_execution_receipt': len(used),
        'native_execution_ledger_window': 5000,
        'satellites_from_live_combo': len(satellites),
        'satellites_with_native_execution_receipt': len({r[1] for r in completed} & satellites),
        'central_native_jobs_with_ack': sum(r[1] == 'central' for r in completed),
        'satellite_access_path': 'existing_signed_COMBO_and_leased_native_queue',
        'execution_owner': 'existing_shared_worker_queue_and_native_wordpress_callbacks',
        'all_business_effects_verified': False}


def tool_context(directory, query):
    if not (directory / 'autonomous-agent' / 'wordpress-tools.sqlite3').exists():
        return []
    words = set(re.findall(r'\w{4,}', query.lower()))
    with connect(directory) as db:
        rows = [json.loads(r[0]) for r in db.execute('SELECT body FROM inventory')]
    rows.sort(key=lambda r: -sum(w in json.dumps(r, ensure_ascii=False).lower() for w in words))
    return [{k: r[k] for k in ('id', 'name', 'capabilities', 'scheduled_hooks', 'native_probe') if k in r} for r in rows[:6]]


def discover(directory):
    from .federation import gateway
    from .module_bus import synchronize, probe
    first = gateway('modules_catalog', {'offset': 0})
    sha, total = first.get('catalog_sha256'), first.get('total')
    if not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{64}', sha) or type(total) is not int or not 0 <= total <= 10000:
        raise ValueError('agent_wordpress_catalogue_contract')
    local = directory / 'module-bus.sqlite3'
    rows = []
    if local.exists():
        with sqlite3.connect(local, timeout=10) as db:
            rows = [json.loads(r[0]) for r in db.execute('SELECT observation_json FROM modules WHERE catalog_sha256=?', (sha,))]
    if len(rows) != total:
        synchronize({'catalog_sha256': sha}, directory)
        with sqlite3.connect(local, timeout=10) as db:
            rows = [json.loads(r[0]) for r in db.execute('SELECT observation_json FROM modules WHERE catalog_sha256=?', (sha,))]
    active = sorted([r for r in rows if r.get('active') is True], key=lambda r: r['id'])
    with connect(directory) as db:
        db.execute('DELETE FROM inventory')
        db.executemany('INSERT INTO inventory VALUES(?,?)', [(r['id'], encode(r).decode()) for r in active])
        row = db.execute("SELECT value FROM settings WHERE name='probe_cursor'").fetchone()
        cursor = int(row[0]) if row else 0
    candidates = [r['id'] for r in active if r.get('native_probe')]
    chosen = [candidates[(cursor + i) % len(candidates)] for i in range(min(4, len(candidates)))]
    observed = probe({'ids': chosen, 'catalog_sha256': sha}, directory) if chosen else {'modules': []}
    with connect(directory) as db:
        db.execute("INSERT INTO settings VALUES('probe_cursor',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (str(cursor + len(chosen)),))
        if db.execute('SELECT COUNT(*) FROM inventory').fetchone()[0] != len(active):
            raise ValueError('agent_wordpress_inventory_readback')
    from .fleet_scheduler import fetch_roster, public_base
    roster = fetch_roster()
    if not isinstance(roster, list) or len(roster) > 100:
        raise ValueError('agent_satellite_roster')
    sites = {}
    for item in roster:
        ident = item.get('site_id') if isinstance(item, dict) else None
        if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', ident):
            raise ValueError('agent_satellite_identity')
        sites[ident] = public_base(item.get('url'), resolve=False)
    with connect(directory) as db:
        db.execute('DELETE FROM satellites')
        db.executemany('INSERT INTO satellites VALUES(?,?)', list(sites.items()))
    with connect(directory) as db:
        if dict(db.execute('SELECT id,url FROM satellites')) != sites:
            raise ValueError('agent_satellite_roster_readback')
    return {**summary(directory), 'catalog_sha256': sha, 'storage_readback_verified': True,
        'probed_this_cycle': [{'id': r['id'], 'native_read_executed': r.get('native_read_executed') is True,
                               'native_error': r.get('native_error')} for r in observed['modules']],
        'inactive_modules_changed': 0}
