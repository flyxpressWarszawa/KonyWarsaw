"""Opt-in, bounded inspection of the installed MSW Hermes WordPress bridge.

This adapter never claims jobs, calls models, invokes callbacks, performs repairs,
or grants Hermes execution authority. Importing it does not make a request.
"""
from __future__ import annotations

import json
import os
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPSHandler, build_opener

from .client import NoRedirect, signed_headers

HOST = 'mobilny-serwis-warszawa.pl'
PATH = '/wp-json/msw-hermes-repair/v1/status'
REMOTE_SCHEMA = 'msw.hermes.agent-repair.v1'
LOCAL_SCHEMA = 'msw.hermes.wordpress.readonly.v1'


def _result(state: str, code: str) -> dict:
    return {
        'schema': LOCAL_SCHEMA,
        'state': state,
        'code': code,
        'wordpress_metadata_observed': state == 'OBSERVED_ONLY',
        'hermes_control_channel_verified': False,
        'signed_stop_hold_epoch_verified': False,
        'production_execution_authorized': False,
        'provider_calls': 0,
        'job_claims': 0,
        'automatic_reissue': False,
    }


def _strict_status(data: object) -> bool:
    if not isinstance(data, dict) or set(data) != {'schema', 'dependencies', 'automatic_reissue'}:
        return False
    if data['schema'] != REMOTE_SCHEMA or data['automatic_reissue'] is not False:
        return False
    deps = data['dependencies']
    return (isinstance(deps, dict)
            and set(deps) == {'remote_store', 'runtime_bridge', 'database_reader'}
            and all(deps[key] is True for key in ('remote_store', 'runtime_bridge', 'database_reader')))


def inspect_wordpress_hermes(site_url: str, node: str, secret: str, *, opener=None) -> dict:
    """Perform ONE signed, read-only status request. No retries or fallback.

    The existing WordPress route is POST by contract but is strictly metadata
    inspection. This adapter never calls /receipt, /gateway, or paid routes.
    It fails closed on all uncertainty, including a 403 from WordPress.
    """
    if not isinstance(site_url, str):
        raise ValueError('hermes_status_origin')
    origin = urlsplit(site_url)
    if (origin.scheme != 'https' or origin.netloc != HOST or origin.path not in ('', '/')
            or origin.query or origin.fragment):
        raise ValueError('hermes_status_origin')
    if (not isinstance(node, str) or re.fullmatch(r'[A-Za-z0-9_-]{1,64}', node) is None
            or not isinstance(secret, str) or len(secret) < 32 or secret.startswith('CHANGE_')):
        raise ValueError('hermes_status_credentials')
    raw = b'{}'
    req = Request('https://' + HOST + PATH, data=raw,
                  headers=signed_headers('status', node, secret, raw), method='POST')
    if opener is None:
        opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(req, timeout=12) as response:
            if response.getcode() != 200:
                return _result('HOLD', 'non_success_status')
            body = response.read(4097)
        if len(body) > 4096:
            return _result('HOLD', 'response_limit')
        data = json.loads(body)
    except HTTPError as error:
        # Never expose response body: it may contain plugin or proxy details.
        return _result('HOLD', 'http_' + str(error.code))
    except (URLError, TimeoutError, OSError, ValueError, UnicodeError):
        return _result('HOLD', 'transport_or_contract_unavailable')
    if not _strict_status(data):
        return _result('HOLD', 'contract_mismatch')
    return _result('OBSERVED_ONLY', 'readonly_wordpress_dependencies')

def main() -> int:
    """One-shot operator inspection. Disabled by default; no autonomous polling.

    Running a signed status request will update the existing WordPress anti-replay
    nonce ledger. This does not grant job, callback, or provider permissions.
    No secrets, request headers, or server response bodies are printed.
    """
    if os.environ.get('MSW_HERMES_WP_STATUS_ONCE') != '1':
        print(json.dumps(_result('HOLD', 'explicit_opt_in_required'), sort_keys=True))
        return 2

    try:
        result = inspect_wordpress_hermes(
            os.environ.get('MSW_WP_URL', ''),
            os.environ.get('MSW_NODE', ''),
            os.environ.get('MSW_SECRET', ''),
        )
    except ValueError:
        result = _result('HOLD', 'invalid_local_configuration')
    except Exception:
        result = _result('HOLD', 'inspection_unavailable')

    print(json.dumps(result, sort_keys=True))
    return 0 if result['state'] == 'OBSERVED_ONLY' else 2


if __name__ == '__main__':
    raise SystemExit(main())
