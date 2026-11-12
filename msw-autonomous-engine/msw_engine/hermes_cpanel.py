"""Hermes read-only broker: five authenticated cPanel reads via the signed WordPress lane."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import ssl
import threading
import time
import uuid
from urllib.request import HTTPSHandler, Request, build_opener

from .client import NoRedirect

HOSTS = frozenset({
    'sxb1plzcpnl507797.prod.sxb1.secureserver.net',
    'sxb1plzcpnl508174.prod.sxb1.secureserver.net',
    'sxb1plzcpnl508218.prod.sxb1.secureserver.net',
    'sxb1plzcpnl508929.prod.sxb1.secureserver.net',
    'sxb1plzcpnl505736.prod.sxb1.secureserver.net',
})
ROOT = 'https://mobilny-serwis-warszawa.pl/wp-json/msw-hermes-cpanel/v1'


def request(endpoint: str, body: dict, secret: str, *, clock=time.time, opener=None) -> dict:
    if endpoint not in {'status', 'receipt'} or not re.fullmatch(r'[a-f0-9]{64}', secret):
        raise ValueError('broker_request_invalid')
    raw = json.dumps(body, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
    moment, nonce = str(int(clock())), str(uuid.uuid4())
    signature = hmac.new(secret.encode('ascii'), moment.encode()+b'\n'+nonce.encode()+b'\n'+raw, hashlib.sha256).hexdigest()
    req = Request(ROOT+'/'+endpoint, data=raw, method='POST', headers={
        'Content-Type': 'application/json', 'Accept': 'application/json',
        'X-MSW-Time': moment, 'X-MSW-Nonce': nonce, 'X-MSW-Signature': signature,
    })
    opener = opener or build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(req, timeout=65) as result:
        if result.status != 200 or 'application/json' not in result.headers.get('Content-Type', ''):
            raise ValueError('broker_http_failure')
        raw_result = result.read(16385)
    if len(raw_result) > 16384:
        raise ValueError('broker_response_limit')
    parsed = json.loads(raw_result)
    if not isinstance(parsed, dict):
        raise ValueError('broker_response_shape')
    return parsed


def once(secret: str, *, call=request) -> dict:
    identity = str(uuid.uuid4())
    result = call('status', {'request_id': identity}, secret)
    evidence = call('receipt', {'request_id': identity}, secret)
    if (result != evidence or result.get('schema') != 'msw.hermes.cpanel.receipt.v1'
            or result.get('request_id') != identity or not isinstance(result.get('results'), dict)
            or set(result['results']) != HOSTS):
        raise ValueError('broker_receipt_mismatch')
    checked = result.get('checked_at')
    if not isinstance(checked, str):
        raise ValueError('broker_time_missing')
    return {'schema': result['schema'], 'checked_at': checked,
            'verified': all(item.get('verified') is True for item in result['results'].values()),
            'results': {host: {'verified': item.get('verified') is True,
                               'main_domain': item.get('main_domain')} for host, item in result['results'].items()},
            'receipt_sha256': result.get('sha256')}


class CpanelMonitor:
    def __init__(self, data_dir: Path, secret: str, *, interval=600, probe=once):
        if not re.fullmatch(r'[a-f0-9]{64}', secret):
            raise ValueError('broker_secret_invalid')
        self.data_dir, self.secret, self.interval, self.probe = data_dir, secret, interval, probe

    def start(self, stopping) -> threading.Thread:
        thread = threading.Thread(target=self.run, args=(stopping,), name='msw-cpanel-monitor', daemon=True)
        thread.start()
        return thread

    def run(self, stopping) -> None:
        while not stopping():
            try:
                result = self.probe(self.secret)
                path = self.data_dir/'hermes-cpanel-health.json'
                temporary = path.with_suffix('.tmp')
                temporary.write_text(json.dumps(result, sort_keys=True, separators=(',', ':')), encoding='utf-8')
                os.chmod(temporary, 0o600)
                temporary.replace(path)
                print(json.dumps({'hermes_cpanel': {'verified': result['verified'],
                       'accounts': len(result['results']), 'receipt_sha256': result['receipt_sha256'],
                       'checked_at': result['checked_at']}}), flush=True)
            except Exception as error:
                print(json.dumps({'hermes_cpanel': {'verified': False, 'code': type(error).__name__}}), flush=True)
            deadline = time.monotonic() + self.interval
            while not stopping() and time.monotonic() < deadline:
                time.sleep(min(1, max(0, deadline-time.monotonic())))
