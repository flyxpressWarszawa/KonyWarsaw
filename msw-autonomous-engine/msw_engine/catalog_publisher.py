"""Resume the owner's existing catalogue on the existing worker, without new services.

Only a fixed signed WordPress adapter is callable. The remote cursor and media
hashes remain authoritative; uncertain writes are always followed by a fresh read.
No paid provider, new database service, or general-purpose REST proxy is used.
"""
from __future__ import annotations

from .recovery import protected_open

import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, Request, build_opener

from .client import NoRedirect, encode, signed_headers

SCHEMA = "msw.catalog.external.v1"


class CatalogError(RuntimeError):
    pass


class CatalogPublisher:
    def __init__(self, client, directory: Path, *, call=None):
        self.client = client
        parsed = urlsplit(client.wordpress_url)
        if (parsed.scheme != "https" or parsed.hostname != "mobilny-serwis-warszawa.pl"
                or parsed.port not in (None, 443) or parsed.path not in ("", "/")
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("catalog_site_scope")
        self.directory = directory / "catalog-publisher"
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "receipts.sqlite3"
        self.call = call or self.remote
        self.sequence = 0
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY, at INTEGER NOT NULL, receipt TEXT NOT NULL)")
        os.chmod(self.path, 0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def remote(self, operation, **values):
        raw = encode({"schema": SCHEMA, "operation": operation, **values})
        request = Request(self.client.wordpress_url + "/wp-json/msw-catalog-recovery/v1/external",
                          data=raw, headers=signed_headers("status", self.client.node, self.client.secret, raw), method="POST")
        try:
            with protected_open(build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context())), request, timeout=45) as response:
                body = response.read(131073)
            if len(body) > 131072:
                raise CatalogError("catalog_response_limit")
            result = json.loads(body)
            if (not isinstance(result, dict) or result.get("schema") != SCHEMA
                    or result.get("operation") != operation or result.get("ok") is not True
                    or not isinstance(result.get("data"), dict)):
                raise CatalogError("catalog_response_invalid")
            return result["data"]
        except HTTPError as error:
            raise CatalogError("catalog_http_" + str(error.code)) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise CatalogError("catalog_transport_unavailable") from None

    def record(self, result):
        receipt = {"schema": SCHEMA, "at": int(time.time()), **result}
        raw = encode(receipt).decode()
        with self.connect() as db:
            db.execute("INSERT INTO observations(at,receipt) VALUES(?,?)", (receipt["at"], raw))
            db.execute("DELETE FROM observations WHERE id < (SELECT COALESCE(MAX(id),0)-2000 FROM observations)")
        temporary = self.directory / "status.tmp"
        temporary.write_text(raw, encoding="utf-8")
        temporary.replace(self.directory / "status.json")
        return receipt

    def step(self):
        # Disk-backed process lock survives independent queue/thread scheduling.
        with (self.directory / "publisher.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"state": "busy"}
            before = self.call("status")
            catalog, assets = before.get("catalog", {}), before.get("assets", {})
            if not isinstance(catalog, dict) or not isinstance(assets, dict):
                raise CatalogError("catalog_status_shape")
            self.sequence += 1
            pending = assets.get("pending_indices", [])
            sha = assets.get("manifest_sha", "")
            image_ready = (isinstance(pending, list) and bool(pending)
                           and type(pending[0]) is int and pending[0] >= 0
                           and isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{64}", sha))
            product_ready = (catalog.get("mode") == "apply" and catalog.get("state") == "running"
                             and type(catalog.get("cursor")) is int and 0 <= catalog["cursor"] < 500
                             and catalog.get("errors", 0) == 0)
            action = "observe"
            # Alternate assets and products so a missing image cannot starve the catalogue.
            if image_ready and (not product_ready or self.sequence % 2 == 1):
                action = "assets"
                self.call(action, index=pending[0], manifest_sha=sha)
            elif product_ready:
                action = "catalog"
                self.call(action, expected_cursor=catalog["cursor"])
            after = self.call("status") if action != "observe" else before
            current = after.get("catalog", {})
            media = after.get("assets", {})
            result = {"state": "observed" if action == "observe" else "readback_verified",
                      "action": action, "catalog_cursor": current.get("cursor"),
                      "catalog_state": current.get("state"), "catalog_errors": current.get("errors"),
                      "images_verified": media.get("verified"), "images_total": media.get("total"),
                      "new_render_services": 0, "paid_generation_invoked_by_scheduler": False}
            if action == "catalog" and current.get("cursor", -1) <= catalog["cursor"]:
                result["state"] = "waiting_for_product_commit"
            if action == "assets" and media.get("verified", 0) <= assets.get("verified", 0):
                result["state"] = "waiting_for_image_commit"
            return self.record(result)

    def start(self, stopping):
        def run():
            delay = 0
            while not stopping():
                deadline = time.monotonic() + delay
                while not stopping() and time.monotonic() < deadline:
                    time.sleep(min(1, max(0, deadline - time.monotonic())))
                if stopping():
                    return
                try:
                    result = self.step()
                    delay = 90 if result.get("action") != "observe" else 300
                except Exception as error:
                    # Never retry the write directly: every next cycle starts with status.
                    delay = min(900, max(120, delay * 2))
                    code = str(error) if isinstance(error, CatalogError) else type(error).__name__
                    result = self.record({"state": "retry_after_read", "code": code[:80], "retry_in_seconds": delay})
                print(json.dumps({"catalog_publisher": result}), flush=True)
        thread = threading.Thread(target=run, name="msw-catalog-publisher", daemon=True)
        thread.start()
        return thread
