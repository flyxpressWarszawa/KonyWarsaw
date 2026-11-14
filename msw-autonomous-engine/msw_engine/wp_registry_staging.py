"""Opt-in, one-shot Hermes staging verification of the private registry snapshot.

Reads a fixed private GitHub checkpoint and posts its strictly whitelisted,
fresh metadata to the dedicated WordPress registry plugin. Never calls an AI
provider, changes provider activation, or modifies the chat queue.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

SCHEMA = "msw.hermes.wp-ai-registry.snapshot.v1"
CHECKPOINT_SCHEMA = "msw.hermes.wp-ai-integration-checkpoint.v1"
REPO = "flyxpressWarszawa/signal-harbor-companion"
CHECKPOINT_PATH = "knowledge/hermes-api-registry/checkpoint-2026-11-14-30-tasks.json"
WP_HOST = "https://msw-ai.com"
WP_ROUTE = "/wp-json/msw-hermes-registry/v1"
MAX_BODY = 130000


class RegistrySyncError(Exception):
    pass


def _utc(value: str) -> datetime:
    if not isinstance(value, str) or len(value) > 50:
        raise RegistrySyncError("timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise RegistrySyncError("timestamp_invalid") from None


def make_payload(checkpoint: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if not isinstance(checkpoint, dict) or checkpoint.get("schema") != CHECKPOINT_SCHEMA:
        raise RegistrySyncError("checkpoint_schema")
    evidence = checkpoint.get("evidence", {})
    reg = evidence.get("registry", {})
    hermes = evidence.get("hermes", {})
    providers = checkpoint.get("providerRoster")
    routes = checkpoint.get("runtimeRoutes")
    if not isinstance(providers, list) or not isinstance(routes, list):
        raise RegistrySyncError("checkpoint_lists")
    if not 0 < len(providers) <= 200 or not 0 <= len(routes) <= 80:
        raise RegistrySyncError("checkpoint_size")
    rev = reg.get("revision")
    ack = reg.get("lastAckRevision")
    if type(rev) is not int or type(ack) is not int or rev < 0 or ack < 0 or ack > rev:
        raise RegistrySyncError("checkpoint_revision")
    seen = _utc(hermes.get("lastSeen", ""))
    age = (now - seen).total_seconds()
    if age > 1800 or age < -300:
        raise RegistrySyncError("stale_hermes_heartbeat")
    provider_whitelist = ("id", "group", "kind", "registryStatus", "hold", "keyReported")
    route_whitelist = ("id", "provider", "model", "tier", "status", "reason", "lastTest")
    p = [{k: x[k] for k in provider_whitelist if k in x} for x in providers if isinstance(x, dict)]
    r = [{k: x[k] for k in route_whitelist if k in x} for x in routes if isinstance(x, dict)]
    if len(p) != len(providers) or len(r) != len(routes):
        raise RegistrySyncError("checkpoint_entries")
    if len({x.get("id") for x in p}) != len(p) or len({x.get("id") for x in r}) != len(r):
        raise RegistrySyncError("checkpoint_duplicate_ids")
    body = {
        "schema": SCHEMA,
        "registryRevision": rev,
        "lastAckRevision": ack,
        "observedAtUtc": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": "hermes_staging_private_checkpoint",
        "providers": p,
        "routes": r,
        "hermes": {
            "status": hermes.get("status", "UNKNOWN"),
            "routerVersion": hermes.get("routerVersion", "UNKNOWN"),
            "lastSeen": hermes["lastSeen"],
            "paidDailyLimitUsd": evidence.get("safety", {}).get("paidDailyLimitUsd", 0.3),
            "spentUsd": hermes.get("todayPaidSpentUsd", 0),
            "reservedUsd": hermes.get("todayReservedUsd", 0),
        },
    }
    if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > MAX_BODY:
        raise RegistrySyncError("payload_too_large")
    return body


def verify_round_trip(payload: dict, post, read_status) -> dict:
    """A single POST, then a separate GET. Never retry an uncertain POST."""
    receipt = post(payload)
    if (not isinstance(receipt, dict) or receipt.get("stored") is not True
            or receipt.get("registryRevision") != payload["registryRevision"]
            or receipt.get("providerRecords") != len(payload["providers"])
            or receipt.get("runtimeRoutes") != len(payload["routes"])):
        raise RegistrySyncError("write_receipt_invalid")
    digest = receipt.get("digestSha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise RegistrySyncError("write_receipt_digest_invalid")
    status = read_status()
    if (not isinstance(status, dict) or status.get("digestSha256") != digest
            or status.get("registryRevision") != payload["registryRevision"]
            or status.get("providerRecords") != len(payload["providers"])
            or status.get("runtimeRoutes") != len(payload["routes"])):
        raise RegistrySyncError("readback_mismatch")
    return {
        "state": "verified_staging_metadata_only",
        "revision": payload["registryRevision"],
        "providerRecords": len(payload["providers"]),
        "runtimeRoutes": len(payload["routes"]),
        "readyRoutes": status.get("readyRoutes"),
        "digestSha256": digest,
        "inferenceTransportVerified": False,
    }


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request_json(url: str, method: str, headers: dict, body: dict | None = None) -> dict:
    raw = None if body is None else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if raw is not None and len(raw) > MAX_BODY:
        raise RegistrySyncError("payload_too_large")
    request = Request(url, data=raw, headers=headers, method=method)
    opener = build_opener(HTTPSHandler(), _NoRedirect())
    try:
        with opener.open(request, timeout=12) as response:
            data = response.read(MAX_BODY * 2 + 1)
            if len(data) > MAX_BODY * 2:
                raise RegistrySyncError("response_too_large")
            return json.loads(data.decode("utf-8"))
    except HTTPError as exc:
        raise RegistrySyncError("http_" + str(exc.code)) from None
    except (URLError, TimeoutError):
        raise RegistrySyncError("transport_unknown_no_retry") from None
    except (UnicodeError, json.JSONDecodeError):
        raise RegistrySyncError("response_invalid") from None


def read_checkpoint() -> dict:
    token = os.environ.get("MSW_HERMES_GITHUB_TOKEN", "").strip()
    if not token:
        raise RegistrySyncError("github_token_missing")
    url = ("https://api.github.com/repos/" + REPO + "/contents/"
           + quote(CHECKPOINT_PATH, safe="/") + "?ref=main")
    meta = _request_json(url, "GET", {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "msw-hermes-registry-staging/0.1",
    })
    if meta.get("type") != "file" or not isinstance(meta.get("content"), str):
        raise RegistrySyncError("github_file_invalid")
    try:
        raw = base64.b64decode(meta["content"], validate=False)
        if len(raw) > MAX_BODY:
            raise RegistrySyncError("github_file_too_large")
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise RegistrySyncError("github_file_invalid") from None


def run_live_once() -> dict:
    if os.environ.get("MSW_HERMES_WP_REGISTRY_STAGING_ENABLED") != "1":
        return {"state": "disabled"}
    token = os.environ.get("MSW_WP_REGISTRY_SCOPED_TOKEN", "").strip()
    if not token or len(token) != 64:
        return {"state": "scoped_token_missing"}
    try:
        checkpoint = read_checkpoint()
        payload = make_payload(checkpoint)
        headers = {
            "X-MSW-Registry-Token": token,
            "Content-Type": "application/json",
            "User-Agent": "msw-hermes-registry-staging/0.1",
        }
        return verify_round_trip(
            payload,
            lambda b: _request_json(WP_HOST + WP_ROUTE + "/snapshot", "POST", headers, b),
            lambda: _request_json(WP_HOST + WP_ROUTE + "/status", "GET", headers),
        )
    except RegistrySyncError as exc:
        return {"state": "blocked_no_retry", "reason": str(exc)[:80]}
    except Exception:
        return {"state": "uncertain_no_retry"}
