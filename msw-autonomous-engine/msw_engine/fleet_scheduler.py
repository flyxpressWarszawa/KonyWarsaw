"""Wake owner-approved WordPress cron independently of long-running AI jobs."""
from __future__ import annotations

from .recovery import protected_open

import ipaddress
import json
import re
import socket
import ssl
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, Request, build_opener

from .client import NoRedirect, encode, signed_headers


def public_base(value: str, *, resolve: bool = True) -> str:
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\x00-\x20\\]", value):
        raise ValueError("fleet_url")
    u = urlsplit(value)
    if (u.scheme != "https" or not u.hostname or u.username or u.password or u.query or u.fragment
            or u.port not in (None, 443) or not re.fullmatch(r"[a-z0-9.-]+", u.hostname)
            or "." not in u.hostname or re.search(r"(?:^|\.)(?:localhost|local|internal|invalid|test|lan|home|onion)$", u.hostname)
            or re.search(r"(?:^|/)\.\.?(?:/|$)|%2e|%2f|%5c", u.path, re.I)):
        raise ValueError("fleet_url")
    try:
        ipaddress.ip_address(u.hostname)
    except ValueError:
        pass
    else:
        raise ValueError("fleet_ip_literal")
    if resolve:
        addresses = {x[4][0] for x in socket.getaddrinfo(u.hostname, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(x).is_global for x in addresses):
            raise ValueError("fleet_private_address")
    return f"https://{u.hostname}{u.path.rstrip('/')}"


def poke(base: str) -> int:
    base = public_base(base)
    request = Request(base + "/wp-cron.php", headers={"User-Agent": "MSW-Owner-Fleet-Scheduler/108"})
    opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    with protected_open(opener, request, timeout=8) as response:
        response.read(1024)
        return response.status


def fetch_roster() -> list:
    from .federation import gateway
    snapshot = gateway("snapshot")
    # A transient or partial signed snapshot is NOT a revocation of every site.
    # Only an explicitly returned empty list can remove the existing roster.
    if not isinstance(snapshot, dict) or "combo108_roster" not in snapshot:
        raise ValueError("combo_roster_missing")
    roster = snapshot["combo108_roster"]
    if not isinstance(roster, list):
        raise ValueError("combo_roster_shape")
    return roster


def pump(client) -> dict:
    raw = encode({"schema": "msw.combo108.pump-request.v1"})
    base = public_base(client.wordpress_url)
    request = Request(base + "/wp-json/msw-portal-combo/v1/runtime108/pump", data=raw,
                      headers=signed_headers("status", client.node, client.secret, raw), method="POST")
    opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    with protected_open(opener, request, timeout=38) as response:
        body = response.read(16385)
    if len(body) > 16384:
        raise ValueError("pump_response_limit")
    result = json.loads(body)
    if (not isinstance(result, dict) or result.get("schema") != "msw.combo108.pump-result.v1"
            or result.get("state") not in {"ran", "not_due", "busy"}):
        raise ValueError("pump_response")
    return {key: result[key] for key in ("state", "at", "version", "steps") if key in result}


def consume_data(client, data_dir: Path, stopping=lambda: False) -> dict:
    from .runner import Worker
    allowed = ["knowledge.ingest", "combo.analytics"]
    if stopping():
        return {"state": "draining"}
    response = client.send("claim", {"capabilities": allowed})
    if response.get("state") != "claimed":
        return {"state": response.get("state", "unverified")}
    if response.get("job", {}).get("kind") not in allowed:
        raise ValueError("data_lane_capability")
    worker = Worker(client, data_dir, max_seconds=30, memory_mb=512, cancel_check=stopping)
    receipt = worker.execute(response["job"], response["lease_token"])
    return worker.acknowledge(receipt)


class FleetScheduler:
    def __init__(self, central: str, data_dir: Path, *, fetch=fetch_roster, wake=poke, clock=time.time, maintain=None, consume=None):
        self.central = public_base(central, resolve=False)
        self.path = data_dir / "fleet-heartbeat.json"
        self.fetch, self.wake, self.clock = fetch, wake, clock
        self.maintain = maintain
        self.consume = consume
        self.roster: list[dict] = []
        self.refreshed = self.next_refresh = self.next_central = 0.0
        self.next_control = 0.0
        self.cursor = 0
        self.observed: dict[str, dict] = {}
        self.control_plane: dict = {"state": "not_run"}
        self.data_lane: dict = {"state": "not_run"}

    def step(self) -> dict:
        now = self.clock()
        ok = failed = 0
        if now >= self.next_control:
            if self.maintain is not None:
                try:
                    self.control_plane = self.maintain()
                except Exception as error:
                    self.control_plane = {"state": "retry", "code": type(error).__name__}
            self.next_control = now + 60
        if now >= self.next_central:
            try:
                code = self.wake(self.central)
                if not 200 <= code < 300:
                    raise ValueError("central_cron_http")
                ok += 1
            except Exception:
                failed += 1
            self.next_central = now + 300
        if now >= self.next_refresh:
            try:
                source = self.fetch()
                if not isinstance(source, list) or len(source) > 100:
                    raise ValueError("roster_shape")
                roster, seen = [], set()
                for site in source:
                    if not isinstance(site, dict) or not isinstance(site.get("site_id"), str):
                        raise ValueError("roster_identity")
                    base = public_base(site.get("url"), resolve=False)
                    if base == self.central or base in seen:
                        continue
                    seen.add(base)
                    roster.append({"site_id": site["site_id"], "url": base})
                self.roster, self.refreshed = roster, now
                active = {x["site_id"] for x in roster}
                self.observed = {k: v for k, v in self.observed.items() if k in active}
                self.next_refresh = now + 300
            except Exception:
                self.next_refresh = now + 60
        # Never keep waking a removed or revoked roster indefinitely after central outage.
        active_roster = self.roster if now - self.refreshed < 86400 else []
        for _ in range(min(3, len(active_roster))):
            site = active_roster[self.cursor % len(active_roster)]
            self.cursor += 1
            try:
                code = self.wake(site["url"])
                good = 200 <= code < 300
            except Exception:
                code, good = 0, False
            ok += int(good)
            failed += int(not good)
            self.observed[site["site_id"]] = {"requested_at": int(now), "http_status": code,
                                              "cron_request_accepted": good,
                                              "combo_execution_verified": False}
        if self.consume is not None:
            try:
                self.data_lane = self.consume()
            except Exception as error:
                self.data_lane = {"state": "retry", "code": type(error).__name__}
        status = {"schema": "msw.combo108.external-heartbeat.v1", "generated_at": int(now),
                  "approved_satellites": len(active_roster), "roster_refreshed_at": int(self.refreshed),
                  "accepted_this_cycle": ok, "failed_this_cycle": failed,
                  "central_control_plane": self.control_plane,
                  "data_lane": self.data_lane,
                  "cadence_seconds": 60, "satellites_per_cycle": 3, "nodes": self.observed}
        status["central_wp_cron_seconds"] = 300
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(status, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)
        return {k: v for k, v in status.items() if k != "nodes"}

    def start(self, stopping) -> threading.Thread:
        def run():
            while not stopping():
                started = time.monotonic()
                try:
                    print(json.dumps(self.step()), flush=True)
                except Exception as error:
                    print(json.dumps({"state": "fleet_scheduler_retry", "code": type(error).__name__}), flush=True)
                while not stopping() and time.monotonic() - started < 60:
                    time.sleep(1)
        thread = threading.Thread(target=run, name="msw-owner-fleet-heartbeat", daemon=True)
        thread.start()
        return thread
