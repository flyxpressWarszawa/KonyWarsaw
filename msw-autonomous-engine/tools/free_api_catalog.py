#!/usr/bin/env python3
"""MSW public API catalogue: bounded, evidence-based import. Python stdlib only.

Usage:
  python tools/free_api_catalog.py --input data.json --output-dir output
  python tools/free_api_catalog.py --input sources.csv --output-dir output --limit 20000

Never submit API credentials. Import is offline; URL strings are NEVER fetched or executed.
A discovered URL is not proof of a free working API.
"""
import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

SCHEMA = "msw.hermes.free-api-catalog.v1"
TARGET = 20000
ALLOWED = {"http", "https"}
SECRET_KEYS = {"api_key", "apikey", "key", "token", "access_token", "secret", "password", "authorization", "client_secret"}
SOURCE_FIELDS = ("source_url", "source", "documentation", "docs", "homepage")
URL_FIELDS = ("endpoint", "api_url", "url", "link")

def normal_url(raw):
    if not isinstance(raw, str) or len(raw) > 2048:
        return None
    try:
        p = urlsplit(raw.strip())
        if p.scheme.lower() not in ALLOWED or not p.hostname or p.username or p.password:
            return None
        host = p.hostname.lower()
        if host in ("localhost",) or host.endswith((".local", ".internal")):
            return None
        if re.match(r"^(127\\.|0\\.|10\\.|192\\.168\\.|169\\.254\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.)", host):
            return None
        if ":" in host and not host.startswith("["): # IPv6 is not admitted by this offline importer
            return None
        port = (":" + str(p.port)) if p.port and p.port not in (80, 443) else ""
        query = parse_qsl(p.query, keep_blank_values=True)
        if any(k.lower() in SECRET_KEYS or any(s in k.lower() for s in ("secret", "token", "password")) for k, _ in query):
            return None
        return urlunsplit((p.scheme.lower(), host + port, p.path.rstrip("/") or "/", urlencode(sorted(query)), ""))
    except (ValueError, TypeError):
        return None

def load(path):
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    if path.suffix.lower() in (".jsonl", ".ndjson"):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    for key in ("apis", "entries", "records", "sources", "providers", "items"):
        if isinstance(data.get(key), list):
            return data[key]
    raise ValueError("No records array in JSON")

def collect(paths, limit=TARGET):
    accepted = {}
    rejected = Counter()
    for path in paths:
        for obj in load(path):
            if not isinstance(obj, dict):
                rejected["not_object"] += 1
                continue
            candidate = next((obj[k] for k in URL_FIELDS if isinstance(obj.get(k), str) and obj[k]), None)
            endpoint = normal_url(candidate)
            if not endpoint:
                rejected["unsafe_or_missing_url"] += 1
                continue
            documentation = next((normal_url(obj[k]) for k in SOURCE_FIELDS if isinstance(obj.get(k), str) and normal_url(obj[k])), None)
            if not documentation:
                documentation = endpoint
            # Provider docs and API endpoints both enter as *unverified discovery*, never validated API.
            key = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
            if key in accepted:
                rejected["duplicate_endpoint"] += 1
                continue
            category = str(obj.get("category") or obj.get("group") or "unclassified")[:100]
            accepted[key] = {
                "id": key[:20], "endpoint": endpoint, "source_url": documentation,
                "category": category, "origin_file": path.name,
                "discovery_status": "UNVERIFIED_SOURCE",
                "auth_required": "UNKNOWN", "zero_cost_verified": False,
                "terms_verified": False, "live_http_verified": False,
                "hermes_inference_verified": False, "enabled": False
            }
            if len(accepted) >= limit:
                return list(accepted.values()), rejected
    return list(accepted.values()), rejected

def write_catalog(records, rejected, out):
    out.mkdir(parents=True, exist_ok=True)
    records.sort(key=lambda x: x["id"])
    with (out / "catalog.jsonl").open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\\n")
    report = {
        "schema": SCHEMA, "target_unique_verified_apis": TARGET,
        "discovered_unique_urls": len(records), "verified_apis": 0,
        "verified_zero_cost": 0, "hermes_connections_confirmed": 0,
        "target_remaining_unverified": TARGET,
        "rejections": dict(rejected), "categories": dict(Counter(x["category"] for x in records)),
        "method": "OFFLINE_IMPORT_NO_NETWORK_NO_INFERENCE",
        "warning": "URLs and documentation must never be counted as verified free APIs."
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    return report

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, action="append", required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--limit", type=int, default=TARGET)
    a = p.parse_args()
    if not (1 <= a.limit <= TARGET):
        p.error("--limit must be 1..20000")
    records, rejected = collect(a.input, a.limit)
    print(json.dumps(write_catalog(records, rejected, a.output_dir), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
