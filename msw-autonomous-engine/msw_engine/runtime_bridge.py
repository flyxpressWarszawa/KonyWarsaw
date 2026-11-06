"""Signed native WordPress paid API; credentials stay on the WordPress server."""
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPSHandler, build_opener

from .client import NoRedirect, encode, signed_headers
from .recovery import protected_open

SCHEMA = 'msw.agent.runtime.v1'


def call(operation, values=None):
    from .tasks import TaskError
    base = os.environ.get('MSW_WP_URL', '').rstrip('/')
    url = urlsplit(base)
    node, secret = os.environ.get('MSW_NODE', ''), os.environ.get('MSW_SECRET', '')
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or len(secret) < 32):
        raise TaskError('native_bridge_not_configured')
    if operation not in {'status', 'generate'}:
        raise TaskError('native_bridge_operation')
    raw = encode({'schema': SCHEMA, 'operation': operation, **(values or {})})
    request = Request(base + '/wp-json/msw-agent-runtime/v1/gateway', data=raw,
                      headers=signed_headers('status', node, secret, raw), method='POST')
    try:
        with protected_open(build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context())), request, timeout=85) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise TaskError('native_bridge_response_limit')
        answer = json.loads(raw)
        if (not isinstance(answer, dict) or answer.get('schema') != SCHEMA
                or answer.get('ok') is not True or not isinstance(answer.get('data'), dict)):
            raise TaskError('native_bridge_contract')
        return answer['data']
    except HTTPError as error:
        raise TaskError('native_bridge_http_' + str(error.code)) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise TaskError('native_bridge_transport') from None


def generate(values, *, allow_fallback=False):
    from .tasks import TaskError
    answer = call('generate', values)
    deadline = time.monotonic() + 100
    while answer.get('state') == 'provider_pending':
        if time.monotonic() >= deadline:
            raise TaskError('native_bridge_provider_pending')
        time.sleep(5)
        answer = call('generate', values)
    if answer.get('request_id') != values.get('request_id'):
        raise TaskError('native_bridge_request_identity')
    if answer.get('state') != 'completed':
        code = answer.get('error_code', answer.get('state', 'unknown'))
        if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', code):
            code = 'unknown'
        # Only a definitive response failure can select another configured API.
        # No timeout, unknown outcome or input guard is permission to reissue.
        if (allow_fallback and answer.get('state') == 'provider_failed'
                and not any(s in code for s in ('transport', 'unknown', 'exception', 'guard', 'policy'))):
            status = call('status')
            if status.get('secondary_generation_allowed') is True:
                providers = status.get('existing_api_providers', {})
                for provider in ('gemini', 'groq'):
                    if providers.get(provider, {}).get('configured') is not True:
                        continue
                    attempt = {**values, 'provider': provider,
                               'request_id': 'secondary:' + hashlib.sha256(encode([values, provider])).hexdigest()}
                    try:
                        return generate(attempt)
                    except TaskError as error:
                        if 'transport' in str(error) or 'unknown' in str(error):
                            raise
        raise TaskError('native_bridge_terminal_failure:' + code)
    text = answer.get('text')
    if (not isinstance(text, str) or answer.get('answer_sha256') != hashlib.sha256(text.encode()).hexdigest()
            or answer.get('storage_readback_verified') is not True):
        raise TaskError('native_bridge_answer_readback')
    return answer


def inspect(directory: Path):
    observed = call('status')
    rows = observed.get('plugins')
    if (not isinstance(rows, list) or len(rows) > 10000 or any(not isinstance(r, dict) or type(r.get('active')) is not bool or not isinstance(r.get('id'), str) for r in rows)
            or len({r['id'] for r in rows}) != len(rows)
            or observed.get('installed_plugins') != len(rows)
            or observed.get('active_plugins') != sum(r['active'] for r in rows)):
        raise ValueError('native_bridge_inventory_contract')
    root = directory / 'autonomous-agent'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / 'runtime-bridge.json'
    raw = encode(observed)
    tmp = path.with_suffix('.tmp')
    tmp.write_bytes(raw)
    tmp.chmod(0o600)
    tmp.replace(path)
    if path.read_bytes() != raw:
        raise ValueError('native_bridge_inventory_readback')
    paid = observed.get('paid_openai', {})
    probe = root / 'paid-openai-bridge-check.json'
    verified = None
    if probe.exists():
        verified = json.loads(probe.read_text())
    # A known incomplete v1 probe must not permanently cache a false outage.
    # Migrate only this definitive failure, once; keep unknown outcomes pinned.
    retry_incomplete = (isinstance(verified, dict)
                        and verified.get('api_text_verified') is False
                        and verified.get('code') == 'native_bridge_terminal_failure:msw_openai_incomplete'
                        and verified.get('probe_revision', 1) == 1)
    if (verified is None or retry_incomplete) and paid.get('key_configured') is True:
        from .tasks import TaskError
        try:
            answer = generate({'request_id': 'msw-native-openai-bridge-check-v2', 'task': 'review',
                               'text': 'Test połączenia. Odpowiedz wyłącznie MSW-BRIDGE-OK.', 'max_tokens': 2000})
            verified = {'request_id': answer['request_id'], 'model': answer.get('model'),
                        'answer_sha256': answer['answer_sha256'], 'api_text_verified': 'MSW-BRIDGE-OK' in answer['text'],
                        'server_result_readback_verified': answer['storage_readback_verified'], 'at': int(time.time())}
        except TaskError as error:
            verified = {'api_text_verified': False, 'code': str(error), 'at': int(time.time())}
        verified['probe_revision'] = 2
        tmp = probe.with_suffix('.tmp')
        tmp.write_bytes(encode(verified))
        tmp.chmod(0o600)
        tmp.replace(probe)
        if json.loads(probe.read_text()) != verified:
            raise ValueError('native_bridge_probe_readback')
    paypal = next((g.get('enabled') for g in observed.get('payment_gateways', []) if g.get('id') == 'ppcp-gateway'), False)
    return {'state': 'native_bridge_verified', 'installed_plugins': len(rows), 'active_modules': sum(r['active'] for r in rows),
            'inventory_sha256': hashlib.sha256(raw).hexdigest(), 'storage_readback_verified': True,
            'paid_openai': verified or {'api_text_verified': False, 'key_configured': paid.get('key_configured') is True},
            'paypal_gateway_enabled': paypal, 'payment_transaction_tested': False,
            'existing_api_providers': observed.get('existing_api_providers', {}),
            'secondary_generation_allowed': observed.get('secondary_generation_allowed') is True,
            'all_plugin_business_effects_verified': False, 'credential_location': 'wordpress_native_router',
            'new_render_services': 0}
