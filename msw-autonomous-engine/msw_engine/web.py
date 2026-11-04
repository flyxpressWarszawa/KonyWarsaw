"""Optional Render Free API: short jobs only, activated by signed incoming work.

No idle polling loop, no keep-alive requests and no reliance on local durability.
WordPress keeps source jobs/results. Persistent knowledge/training needs a VPS.
"""
from __future__ import annotations

import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
import time

from .client import Client, TransportError, encode
from .runner import Worker
from .tasks import handlers


def main():
    os.umask(0o077)
    os.environ['MSW_RUNTIME_MODE'] = 'render-free'
    base = os.environ.get('MSW_WP_URL', '').rstrip('/')
    client = Client(base, os.environ.get('MSW_TRANSPORT_URL', base + '/wp-content/plugins/msw-unified-engine/transport.php'), os.environ.get('MSW_NODE', ''), os.environ.get('MSW_SECRET', ''))
    worker = Worker(client, Path(os.environ.get('MSW_DATA_DIR', '/tmp/msw-engine')), max_seconds=20, memory_mb=256)
    busy = threading.Lock()
    nonces = {}
    nonce_lock = threading.Lock()

    def run_batch():
        try:
            for _ in range(3):
                result = worker.run_once()
                print(json.dumps(result), flush=True)
                if result['state'] == 'idle':
                    break
            worker.health('batch_finished')
        except TransportError as error:
            print(json.dumps({'state': 'batch_waiting_for_retry', 'code': error.code}), flush=True)
        except Exception as error:
            print(json.dumps({'state': 'batch_error', 'code': type(error).__name__}), flush=True)
        finally:
            busy.release()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, code, value):
            raw = encode(value)
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path != '/health':
                self.respond(404, {'error': 'not_found'})
                return
            self.respond(200, {'state': 'api_ready', 'mode': 'render-free-on-demand', 'busy': busy.locked(),
                               'capabilities': list(handlers()), 'persistent_knowledge_available': False,
                               'last_wordpress_contact': worker.last_server_contact or None})

        def do_POST(self):
            if self.path != '/run':
                self.respond(404, {'error': 'not_found'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError
                raw = self.rfile.read(length)
                node = self.headers.get('X-MSWUE-Node', '')
                stamp = self.headers.get('X-MSWUE-Timestamp', '')
                nonce = self.headers.get('X-MSWUE-Nonce', '')
                supplied = self.headers.get('X-MSWUE-Signature', '')
                if node != client.node or not stamp.isdigit() or abs(time.time() - int(stamp)) > 300 or not re.fullmatch('[a-f0-9]{32,64}', nonce):
                    raise ValueError
                message = '\n'.join(['POST', 'wake', node, stamp, nonce, hashlib.sha256(raw).hexdigest()])
                expected = hmac.new(client.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expected, supplied):
                    raise ValueError
                with nonce_lock:
                    for old in [key for key, seen in nonces.items() if time.time() - seen > 600]:
                        del nonces[old]
                    if nonce in nonces or len(nonces) >= 10000:
                        raise ValueError
                    nonces[nonce] = time.time()
                json.loads(raw)
            except (ValueError, json.JSONDecodeError):
                self.respond(401, {'error': 'authentication_failed'})
                return
            if not busy.acquire(blocking=False):
                self.respond(202, {'state': 'batch_already_running'})
                return
            threading.Thread(target=run_batch, daemon=True).start()
            self.respond(202, {'state': 'batch_accepted'})

    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '10000'))), Handler).serve_forever()


if __name__ == '__main__':
    main()
