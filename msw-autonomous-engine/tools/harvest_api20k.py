#!/usr/bin/env python3
"""MSW: collect up to 20,000 *discovered candidates*, never imply verified/free.

Reads only published public directories. No customer data, API tokens, execution
against discovered endpoints, public page crawling, provider registration or cron.
Usage:
 python msw-autonomous-engine/tools/harvest_api20k.py --output msw-autonomous-engine/knowledge/api20k --max-candidates 20000
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl
from urllib.request import Request, urlopen

TARGET = 20_000
APIDIRECTORY = "https://apidirectory.dev/apis.json"
APIS_GURU = "https://api.apis.guru/v2/list.json"
SWAGGERHUB = "https://api.swaggerhub.com/specs"
MAX_READ_BYTES = 35_000_000
SCHEMA = "msw.hermes.api20k.discovery.v2"
UA = "MSW-API20k-Research/1.0 (public directory reader; no inference)"

def safe_url(candidate):
    """Never persist credentials, secrets, loopback or private-address URLs."""
    if not isinstance(candidate, str) or not candidate or len(candidate) > 2048:
        return None
    try:
        u = urlsplit(candidate.strip())
        if u.scheme.lower() not in ("https", "http") or not u.hostname or u.username or u.password:
            return None
        host = u.hostname.lower()
        if host in ("localhost",) or host.endswith((".local", ".internal", ".localhost")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        if any(k.lower() in ("key","token","apikey","api_key","access_token","password","secret","authorization","client_secret") or
               any(s in k.lower() for s in ("secret","password","token")) for k, _ in parse_qsl(u.query)):
            return None
        port = (":" + str(u.port)) if u.port and u.port not in (80, 443) else ""
        return urlunsplit((u.scheme.lower(), host + port, u.path.rstrip("/") or "/", u.query, ""))
    except ValueError:
        return None

def fetch_json(url, *, tries=3):
    if safe_url(url) != url:  # all source URLs must be canonical and safe
        raise ValueError("unsafe catalog source")
    last = None
    for attempt in range(tries):
        try:
            req = Request(url, headers={"Accept":"application/json", "User-Agent": UA})
            with urlopen(req, timeout=40) as response:
                if response.status != 200:
                    raise ValueError("HTTP status %s" % response.status)
                size = response.headers.get("Content-Length")
                if size and int(size) > MAX_READ_BYTES:
                    raise ValueError("source too large")
                data = response.read(MAX_READ_BYTES + 1)
                if len(data) > MAX_READ_BYTES:
                    raise ValueError("source exceeded byte cap")
            return json.loads(data.decode("utf-8-sig"))
        except HTTPError as exc:
            last = exc
            if exc.code not in (429, 500, 502, 503, 504):
                break
            retry = exc.headers.get("Retry-After", "")
            pause = int(retry) if retry.isdigit() else (2 ** attempt)
            time.sleep(min(20, max(1, pause)))
        except (URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            last = exc
            if isinstance(exc, ValueError):
                break
            if attempt + 1 < tries:
                time.sleep(2 ** attempt)
    raise RuntimeError("failed reading %s: %s" % (url, type(last).__name__))

def extracted_list(response):
    if isinstance(response, list):
        return response
    if not isinstance(response, dict):
        return []
    for field in ("apis", "items", "results", "entries", "data", "specs"):
        if isinstance(response.get(field), list):
            return response[field]
        if isinstance(response.get(field), dict):
            for inner in ("apis", "items", "results"):
                if isinstance(response[field].get(inner), list):
                    return response[field][inner]
    return []

class Catalog:
    def __init__(self, max_candidates=TARGET):
        self.max = max_candidates
        self.items = {}
        self.by_source = Counter()
        self.errors = {}
        self.directory_claims_free_no_auth = 0
        self.swaggerhub_pages = 0
        self.source_metadata = {}

    def add(self, *, source, identity, name, docs=None, spec=None, pricing="unknown", auth="unknown", category="unknown"):
        if len(self.items) >= self.max:
            return False
        if not identity or not name:
            return False
        doc = safe_url(docs)
        specurl = safe_url(spec)
        if not doc and not specurl:
            return False
        digest = hashlib.sha256((source + "\0" + str(identity)).encode()).hexdigest()
        if digest in self.items:
            return False
        pricing = str(pricing).strip().lower()[:40]
        auth = str(auth).strip().lower()[:40]
        claim = (pricing == "free" and auth in ("none", "no", "false", "no auth", "not required"))
        record = {
            "id": digest[:24], "source": source, "source_identity": str(identity)[:300],
            "name": str(name)[:240], "category": str(category)[:80],
            "docs_url": doc, "spec_url": specurl, "directory_claims_free_no_auth": claim,
            "pricing_claim": pricing, "auth_claim": auth,
            "status": "DISCOVERED_UNVERIFIED", "actual_endpoint_tested": False,
            "pricing_for_msw_verified": False, "no_login_verified": False,
            "commercial_terms_verified": False, "hermes_ack_verified": False,
            "enabled": False,
        }
        self.items[digest] = record
        self.by_source[source] += 1
        if claim:
            self.directory_claims_free_no_auth += 1
        return True

    def ingest_apidirectory(self, data):
        records = extracted_list(data)
        self.source_metadata["apidirectory"] = {"received": len(records), "source": APIDIRECTORY}
        for row in records:
            if not isinstance(row, dict):
                continue
            slug = row.get("slug") or row.get("id")
            if not slug:
                continue
            self.add(source="apidirectory-curated", identity=slug,
                name=row.get("name") or slug, docs=row.get("docsUrl") or row.get("url"),
                pricing=row.get("pricing") or "unknown", auth=row.get("auth") or "unknown",
                category=row.get("category") or "unknown")

    def ingest_apis_guru(self, data):
        if not isinstance(data, dict):
            return
        self.source_metadata["apis_guru"] = {"received": len(data), "source": APIS_GURU}
        for api_id, value in data.items():
            if not isinstance(value, dict):
                continue
            versions = value.get("versions") or {}
            preferred = value.get("preferred")
            ver = versions.get(preferred, {}) if preferred in versions else next(iter(versions.values()), {})
            if not isinstance(ver, dict):
                ver = {}
            info = ver.get("info") or {}
            if not isinstance(info, dict):
                info = {}
            self.add(source="apis-guru", identity=api_id,
                name=info.get("title") or api_id,
                docs=info.get("x-origin", [{}])[0].get("url") if isinstance(info.get("x-origin"), list) and info.get("x-origin") and isinstance(info["x-origin"][0], dict) else info.get("x-providerName"),
                spec=ver.get("swaggerUrl") or ver.get("swaggerYamlUrl"),
                pricing="unknown", auth="unknown", category="api-spec")

    def ingest_swaggerhub_page(self, data):
        rows = extracted_list(data)
        n = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("name") or row.get("title")
            properties = row.get("properties") or []
            spec = row.get("url") or row.get("specUrl") or row.get("definitionUrl")
            if not spec and isinstance(properties, list):
                for prop in properties:
                    if isinstance(prop, dict) and "url" in prop and safe_url(prop["url"]):
                        spec = prop["url"]
                        break
            owner = row.get("owner") or row.get("provider") or "unknown"
            identity = spec or (str(owner) + "/" + str(name))
            if self.add(source="swaggerhub-public-registry", identity=identity, name=name,
                        docs=row.get("url") or row.get("documentationUrl"), spec=spec,
                        category="api-spec", pricing="unknown", auth="unknown"):
                n += 1
        self.swaggerhub_pages += 1
        return len(rows), n

    def harvest(self, max_swaggerhub_pages):
        try:
            self.ingest_apidirectory(fetch_json(APIDIRECTORY))
        except RuntimeError as exc:
            self.errors["apidirectory"] = str(exc)
        try:
            self.ingest_apis_guru(fetch_json(APIS_GURU))
        except RuntimeError as exc:
            self.errors["apis_guru"] = str(exc)
        prev_signature = None
        for offset in range(0, max_swaggerhub_pages * 100, 100):
            if len(self.items) >= self.max:
                break
            url = SWAGGERHUB + "?" + urlencode({
                "limit": 100, "offset": offset, "specType": "API", "visibility": "PUBLIC", "sort": "NAME", "order": "ASC"
            })
            try:
                page = fetch_json(url)
            except RuntimeError as exc:
                self.errors["swaggerhub_at_offset_%d" % offset] = str(exc)
                break
            rows = extracted_list(page)
            if not rows:
                break
            signature = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if signature == prev_signature:
                self.errors["swaggerhub"] = "repeating page detected, stopping"
                break
            prev_signature = signature
            received, added = self.ingest_swaggerhub_page(page)
            if offset % 2000 == 0:
                print("SwaggerHub offset=%d received=%d added=%d total=%d" %
                      (offset, received, added, len(self.items)), flush=True)
            if received < 100:
                break
            time.sleep(1.1)  # deliberately modest public API traffic; no quota evasion

    def write(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        # Replace only files owned by this discovery pipeline; no secret or customer content.
        for stale in directory.glob("candidates-*.jsonl"):
            stale.unlink()
        items = sorted(self.items.values(), key=lambda x: (x["source"], x["id"]))
        shard = 1000
        hashes = {}
        for i in range(0, len(items), shard):
            file = directory / ("candidates-%03d.jsonl" % (i // shard + 1))
            content = "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n"
                              for row in items[i:i+shard])
            file.write_text(content, encoding="utf-8")
            hashes[file.name] = hashlib.sha256(content.encode()).hexdigest()
        summary = {
            "schema": SCHEMA, "generated_utc": datetime.now(timezone.utc).isoformat(),
            "target_candidates": TARGET, "candidate_registry_entries": len(items),
            "unmet_candidate_target": max(0, TARGET-len(items)),
            "all_candidates_unverified": True,
            "directory_claimed_free_no_auth": self.directory_claims_free_no_auth,
            "independently_verified_free_no_auth": 0,
            "live_hermes_inference_verified": 0,
            "count_basis": "distinct (source, identity), may overlap across registries",
            "by_source": dict(self.by_source),
            "source_metadata": self.source_metadata, "errors": self.errors,
            "sha256_shards": hashes,
            "sources": [APIDIRECTORY, APIS_GURU, SWAGGERHUB],
            "attribution": {
                "apidirectory": "https://apidirectory.dev/terms",
                "apis_guru": "https://github.com/APIs-guru/openapi-directory",
                "swaggerhub": "https://swagger.io/tools/swaggerhub/"
            },
            "warning": "A public spec is not a free or reachable API; do not connect Hermes from this inventory."
        }
        (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return summary

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-candidates", type=int, default=TARGET)
    parser.add_argument("--max-swaggerhub-pages", type=int, default=210)
    args = parser.parse_args()
    if not (1 <= args.max_candidates <= TARGET) or not (0 <= args.max_swaggerhub_pages <= 210):
        parser.error("invalid caps")
    catalog = Catalog(args.max_candidates)
    catalog.harvest(args.max_swaggerhub_pages)
    print(json.dumps(catalog.write(args.output), indent=2, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
