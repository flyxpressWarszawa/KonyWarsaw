"""FotoAtlas delivery journal and asynchronous vision polling on the existing worker.

WordPress owns eligibility, source hashes, model credentials and final native FAU
records. Render owns the long-lived processing loop and crash recovery. No new
Render service or filesystem-wide media copy is created.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener

from .client import NoRedirect, TransportError, encode, signed_headers


SCHEMA = "msw.fotoatlas.render.v1"


class AtlasClient:
    def __init__(self, client):
        self.client = client

    def call(self, action: str, **values) -> dict:
        if action not in {"status", "claim", "submit", "poll", "commit"}:
            raise ValueError("atlas_action")
        raw = encode({"schema": SCHEMA, "action": action, **values})
        request = Request(self.client.wordpress_url + "/wp-json/msw-fotoatlas-render/v1/control",
                          data=raw, method="POST", headers=signed_headers("status", self.client.node, self.client.secret, raw))
        try:
            with build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context())).open(request, timeout=35) as response:
                body = response.read(65537)
            if len(body) > 65536:
                raise TransportError("atlas_response_limit")
            result = json.loads(body)
            if not isinstance(result, dict) or "code" in result:
                raise TransportError("atlas_response_schema")
            return result
        except HTTPError as error:
            # WordPress may return structured prerequisite errors or a host error page.
            # Report recognised codes only. Never log HTML, headers or provider payloads.
            code = "atlas_bridge_http"
            try:
                detail = json.loads(error.read(4096)).get("code")
                allowed = {"msw_atlas_render_" + value for value in (
                    "owner_required", "scope", "transport_missing", "native_dependencies",
                    "provider_missing", "claim_readback", "claim_receipt_readback",
                    "reservation_readback", "lease", "arguments"
                )}
                if isinstance(detail, str) and detail in allowed:
                    code = detail
            except (ValueError, AttributeError, OSError):
                pass
            raise TransportError(code, error.code) from None
        except (URLError, OSError, ValueError):
            raise TransportError("atlas_bridge_unavailable") from None


class FotoAtlas:
    def __init__(self, client, directory: Path, *, clock=time.time):
        self.client, self.clock = client, clock
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "fotoatlas-deliveries.sqlite3"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS deliveries (id INTEGER PRIMARY KEY,lease TEXT NOT NULL,sha TEXT NOT NULL,state TEXT NOT NULL,receipt TEXT NOT NULL DEFAULT '{}',next_at INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def claim(self) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='claim_sequence'").fetchone()
            sequence = int(row[0]) if row else 0
        request_id = "fotoatlas-render-claim-" + str(sequence)
        result = self.client.call("claim", request_id=request_id)
        if result.get("state") == "busy":
            return result
        items = result.get("items", [])
        if not isinstance(items, list) or len(items) > 5:
            raise TransportError("atlas_claim_schema")
        with self.connect() as db:
            for item in items:
                if (type(item.get("attachment_id")) is not int or item["attachment_id"] < 1
                        or not isinstance(item.get("lease"), str) or len(item["lease"]) != 48
                        or not isinstance(item.get("source_sha256"), str) or len(item["source_sha256"]) != 64):
                    raise TransportError("atlas_claim_item")
                old = db.execute("SELECT lease,sha FROM deliveries WHERE id=?", (item["attachment_id"],)).fetchone()
                if old and tuple(old) != (item["lease"], item["source_sha256"]):
                    raise TransportError("atlas_delivery_conflict")
                db.execute("INSERT OR IGNORE INTO deliveries(id,lease,sha,state) VALUES(?,?,?,'claimed')", (item["attachment_id"], item["lease"], item["source_sha256"]))
            db.execute("INSERT INTO metadata VALUES('claim_sequence',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(sequence + 1),))
        return {"state": result.get("state", "unknown"), "claimed": len(items)}

    def step(self) -> dict:
        # This journal contains delivery references, never provider keys or images.
        with self.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM deliveries WHERE state IN ('claimed','provider_pending','described','hourly_card_limit','outcome_unknown_do_not_repeat') AND next_at<=? ORDER BY state='provider_pending' DESC,state='described' DESC,state='claimed' DESC,id LIMIT 6", (int(self.clock()),))]
        results = []
        for row in rows:
            action = "submit" if row["state"] == "claimed" else ("commit" if row["state"] in {"described", "hourly_card_limit"} else "poll")
            result = self.client.call(action, attachment_id=row["id"], lease=row["lease"])
            state = result.get("state")
            if state == "busy":
                continue
            if state == "committed" and (result.get("attachment_id") != row["id"] or result.get("native_readback_verified") is not True):
                raise TransportError("atlas_commit_ack")
            if state not in {"claimed", "provider_pending", "described", "committed", "review", "hourly_card_limit", "hourly_photo_limit", "outcome_unknown_do_not_repeat"}:
                raise TransportError("atlas_result_state")
            local_state = "claimed" if state == "hourly_photo_limit" else state
            next_at = (int(self.clock()) // 3600 + 1) * 3600 if state in {"hourly_card_limit", "hourly_photo_limit"} else (int(self.clock()) + 60 if state == "outcome_unknown_do_not_repeat" else 0)
            with self.connect() as db:
                db.execute("UPDATE deliveries SET state=?,receipt=?,next_at=? WHERE id=?", (local_state, json.dumps(result, ensure_ascii=False), next_at, row["id"]))
            results.append({"attachment_id": row["id"], "state": state,
                            "native_public_details_verified": result.get("native_public_details_verified", False)})
        with self.connect() as db:
            active = db.execute("SELECT COUNT(*) FROM deliveries WHERE state IN ('claimed','provider_pending')").fetchone()[0]
            counts = {row[0]: row[1] for row in db.execute("SELECT state,COUNT(*) FROM deliveries GROUP BY state")}
        claim = self.claim() if active < 6 else {"state": "active_leases", "count": active}
        return {"schema": "msw.fotoatlas.render-worker.v1", "at": int(self.clock()),
                "photo_limit": 500, "card_limit": 200, "results": results, "journal_counts": counts, "claim": claim}

    def start(self, stopping) -> threading.Thread:
        def run():
            delay = 2
            while not stopping():
                try:
                    result = self.step()
                    print(json.dumps({"fotoatlas_render": result}), flush=True)
                    delay = 60 if not result["results"] and result["claim"].get("state") in {"no_eligible_work", "hourly_photo_limit"} else 2
                except Exception as error:
                    data = {"state": "retry", "code": type(error).__name__}
                    if isinstance(error, TransportError):
                        data.update(code=error.code, http_status=error.status)
                    print(json.dumps({"fotoatlas_render": data}), flush=True)
                    delay = min(300, max(30, delay * 2))
                until = time.monotonic() + delay
                while not stopping() and time.monotonic() < until:
                    time.sleep(min(1, max(0, until - time.monotonic())))
        thread = threading.Thread(target=run, name="msw-fotoatlas-render", daemon=True)
        thread.start()
        return thread
