"""Bounded cPanel UAPI health probe; never accepts arbitrary commands or URLs."""
import json
import os
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener

HOST = 'sxb1plzcpnl508218.prod.sxb1.secureserver.net'
URL = 'https://' + HOST + ':2083/execute/ServerInformation/get_information'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(environ=None, opener=None):
    env = os.environ if environ is None else environ
    username, token = env.get('MSW_CPANEL_USER', ''), env.get('MSW_CPANEL_API_TOKEN', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', username) or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', token):
        return {'state': 'not_configured', 'verified': False}
    request = Request(URL, headers={'Authorization': 'cpanel ' + username + ':' + token, 'Accept': 'application/json'})
    opener = opener or build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(request, timeout=15) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            return {'state': 'response_too_large', 'verified': False}
        body = json.loads(raw)
        result = body.get('result') if isinstance(body, dict) else None
        if not isinstance(result, dict) or result.get('status') != 1:
            return {'state': 'api_rejected', 'verified': False}
        data = result.get('data')
        return {'state': 'connected', 'verified': True, 'records': len(data) if isinstance(data, list) else None}
    except HTTPError as error:
        return {'state': 'http_error', 'http_status': error.code, 'verified': False}
    except (URLError, OSError, TimeoutError, ValueError, TypeError):
        return {'state': 'unavailable_or_invalid_response', 'verified': False}


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False))
