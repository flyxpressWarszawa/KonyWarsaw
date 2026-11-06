"""Direct scoped business tools on the owner's existing WordPress credentials."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPSHandler, build_opener

from .client import encode, signed_headers, NoRedirect
from .recovery import protected_open


class BusinessBridge:
    def __init__(self, client, directory: Path, *, remote=None):
        if client.wordpress_url != 'https://mobilny-serwis-warszawa.pl':
            raise ValueError('business_site_scope')
        self.client, self.directory = client, directory
        self.root = directory / 'business-bridge'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / 'state.sqlite3'
        self.call = remote or self.remote
        self.reported_configuration = False
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY,body TEXT NOT NULL,sha256 TEXT NOT NULL)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def remote(self, operation, **fields):
        raw = encode({'schema': 'msw.federation.v1', 'operation': 'autonomy_execute' if operation in {'product.publish', 'capability.execute', 'telegram.reply'} else 'snapshot',
                      'business_operation': operation, **fields})
        request = Request(self.client.wordpress_url + '/wp-json/msw-business-agent/v1/gateway', data=raw,
                          headers=signed_headers('status', self.client.node, self.client.secret, raw), method='POST')
        try:
            with protected_open(build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context())), request, timeout=45) as response:
                body = response.read(262145)
            if len(body) > 262144:
                raise ValueError('business_response_limit')
            result = json.loads(body)
            if result.get('schema') != 'msw.business-agent.v1' or result.get('ok') is not True or not isinstance(result.get('data'), dict):
                raise ValueError('business_response_contract')
            return result['data']
        except HTTPError as error:
            raise ValueError('business_http_' + str(error.code)) from None
        except (URLError, OSError, TimeoutError):
            raise ValueError('business_transport_unavailable') from None

    def position(self, name):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE name=?', (name,)).fetchone()
        return int(row[0]) if row else 0

    def record(self, ident, result):
        raw = encode(result).decode()
        sha = hashlib.sha256(raw.encode()).hexdigest()
        with self.connect() as db:
            db.execute('INSERT INTO receipts VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,sha256=excluded.sha256', (ident, raw, sha))
        with self.connect() as db:
            if db.execute('SELECT body,sha256 FROM receipts WHERE id=?', (ident,)).fetchone() != (raw, sha):
                raise ValueError('business_receipt_readback')
        return sha

    def step(self):
        status = self.call('status', request_id='business-status-' + str(int(time.time()) // 300))
        self.record('status', status)
        if status.get('owner_authorized') is not True:
            raise ValueError('business_owner_grant')
        if not self.reported_configuration:
            print(json.dumps({'business_connection_configuration': {
                'paypal': status.get('paypal', {}), 'telegram': status.get('telegram', {}),
                'owner_authorized': True}}, ensure_ascii=False), flush=True)
            self.reported_configuration = True
        # Public pages become source memory, with an independent source/readback.
        offset = self.position('content_offset')
        page = self.call('context', request_id='business-context-' + str(offset), offset=offset)
        if page.get('origin') != self.client.wordpress_url or not isinstance(page.get('records'), list) or len(page['records']) > 40:
            raise ValueError('business_context_scope')
        records = []
        for item in page['records']:
            if type(item.get('id')) is not int or not str(item.get('url', '')).startswith(self.client.wordpress_url + '/'):
                raise ValueError('business_context_source')
            records.append({'id': 'business:wp:' + str(item['id']), 'approved': True,
                'title': str(item.get('title', ''))[:1000], 'text': str(item.get('excerpt', ''))[:1200],
                'url': item['url'], 'provenance': 'authenticated_live_published_wordpress_content_not_independent_fact_verification'})
        from .tasks import knowledge_ingest
        imported = knowledge_ingest({'records': records}, self.directory)
        if imported['readback_verified_records'] != len(records) - len(imported.get('skipped_empty', [])):
            raise ValueError('business_context_readback')
        next_offset = page.get('next_offset')
        if next_offset is not None and (type(next_offset) is not int or next_offset <= offset):
            raise ValueError('business_context_cursor')
        with self.connect() as db:
            db.execute("INSERT INTO settings VALUES('content_offset',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (str(next_offset or 0),))
        product_offset = self.position('product_offset')
        products = self.call('products', request_id='business-products-' + str(product_offset), offset=product_offset)
        self.record('products', products)
        published = []
        for product in products.get('records', [])[:30]:
            if product.get('status') != 'draft' or product.get('qa_receipt_present') is not True:
                continue
            ident = 'business-publish-' + str(product['id']) + '-' + product['sha256'][:24]
            result = self.call('product.publish', request_id=ident, product_id=product['id'], expected_sha256=product['sha256'])
            self.record(ident, result)
            if result.get('storage_readback_verified') is True:
                published.append(product['id'])
        next_product = products.get('next_offset')
        if next_product is not None and (type(next_product) is not int or next_product <= product_offset):
            raise ValueError('business_product_cursor')
        with self.connect() as db:
            db.execute("INSERT INTO settings VALUES('product_offset',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (str(next_product or 0),))
        capabilities = self.call('capabilities', request_id='business-capabilities-' + str(int(time.time()) // 300))
        self.record('capabilities', capabilities)
        tools = []
        for capability in capabilities.get('capabilities', [])[:1000]:
            if not isinstance(capability, dict) or not isinstance(capability.get('id'), str):
                raise ValueError('business_capability_contract')
            tools.append({'id': 'business:tool:' + capability['id'], 'approved': True,
                'title': 'Aktywne narzędzie MSW: ' + capability['id'],
                'text': json.dumps(capability, ensure_ascii=False)[:12000],
                'provenance': 'authenticated_active_central_registry_tool_schema_not_execution_evidence'})
        for start in range(0, len(tools), 100):
            batch = tools[start:start + 100]
            if knowledge_ingest({'records': batch}, self.directory)['readback_verified_records'] != len(batch):
                raise ValueError('business_capabilities_readback')
        summary = {'state': 'completed', 'content_records_readback': imported['readback_verified_records'],
            'active_tools_catalogued': len(tools),
            'products_observed': len(products.get('records', [])), 'products_published_readback': published,
            'paypal_enabled': status.get('paypal', {}).get('enabled') is True,
            'telegram_transport_verified': status.get('telegram', {}).get('customer_reply_transport_verified') is True,
            'owner_authorized': status.get('owner_authorized') is True, 'storage_readback_verified': True}
        self.record('last_cycle', summary)
        return summary

    def start(self, stopping):
        def run():
            delay = 0
            while not stopping():
                for _ in range(delay):
                    if stopping(): return
                    time.sleep(1)
                if stopping(): return
                try:
                    result = self.step(); delay = 300
                except Exception as error:
                    delay = min(900, max(120, delay * 2))
                    result = {'state': 'retry', 'code': str(error)[:90] if isinstance(error, ValueError) else type(error).__name__, 'retry_in_seconds': delay}
                print(json.dumps({'business_bridge': result}), flush=True)
        thread = threading.Thread(target=run, name='msw-business-bridge', daemon=True)
        thread.start()
        return thread


def status(directory):
    path = directory / 'business-bridge' / 'state.sqlite3'
    if not path.exists(): return {'state': 'not_observed'}
    with sqlite3.connect(path, timeout=10) as db:
        row = db.execute("SELECT body,sha256 FROM receipts WHERE id='last_cycle'").fetchone()
        config = db.execute("SELECT body FROM receipts WHERE id='status'").fetchone()
    result = json.loads(row[0]) if row and hashlib.sha256(row[0].encode()).hexdigest() == row[1] else {'state': 'no_completed_cycle'}
    if config:
        settings = json.loads(config[0]); result['paypal'] = settings.get('paypal', {}); result['telegram'] = settings.get('telegram', {})
    return result
