"""Read only fresh, scoped cPanel account telemetry using the existing node signature."""
from __future__ import annotations

import json
import math
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .client import encode, signed_headers
from .recovery import protected_open

SCHEMA = 'msw.render.account-health.v1'


def read_account_health(client, *, clock=time.time):
    unavailable = {'state': 'unavailable'}
    raw = encode({'schema': SCHEMA})
    request = Request(client.wordpress_url + '/wp-content/plugins/msw-unified-engine/msw-render-account-health.php',
                      data=raw, headers=signed_headers('status', client.node, client.secret, raw), method='POST')
    try:
        with protected_open(client.opener, request, timeout=12) as response:
            body = response.read(8193)
        if len(body) > 8192:
            return unavailable
        value = json.loads(body)
        if not isinstance(value, dict) or value.get('schema') != SCHEMA or value.get('state') != 'fresh':
            return unavailable
        at, cpu, memory = value.get('measured_at'), value.get('cpu_percent'), value.get('physical_memory_percent')
        if (type(at) is not int or at > int(clock()) or int(clock()) - at > 90
                or type(cpu) not in (int, float) or type(memory) not in (int, float)
                or not math.isfinite(cpu) or not math.isfinite(memory)
                or not (0 <= cpu <= 100 and 0 <= memory <= 100)):
            return unavailable
        return {'state': 'fresh', 'at': at, 'cpu_percent': cpu, 'physical_memory_percent': memory}
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError, sqlite3.Error):
        return unavailable
