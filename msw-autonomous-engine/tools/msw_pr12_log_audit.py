#!/usr/bin/env python3
"""Read-only audit of exported Render auxiliary-worker JSON/JSONL logs.

No network calls, WordPress credentials, job submission, or production writes.
Log export must cover the full time range for a complete aggregate.
"""
import argparse
import json
from pathlib import Path
from collections import Counter


def read_records(path: Path):
    text = path.read_text(encoding="utf-8")
    if text.lstrip().startswith("[") or text.lstrip().startswith("{"):
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            document = None
        if isinstance(document, list):
            return document
        if isinstance(document, dict):
            return document.get("logs", [document])
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def auxiliary_event(row):
    if not isinstance(row, dict):
        return None
    if isinstance(row.get("message"), str):
        try:
            row = json.loads(row["message"])
        except json.JSONDecodeError:
            return None
    return row.get("render_auxiliary") if isinstance(row.get("render_auxiliary"), dict) else None


def audit(records):
    events = [evt for row in records if (evt := auxiliary_event(row)) is not None]
    names = Counter(e.get("state", "unknown") for e in events)
    starts = [e for e in events if e.get("state") == "started"]
    admits = [e for e in events if e.get("state") == "batch_admitted"]
    finishes = [e for e in events if e.get("state") == "batch_finished"]
    completions = [e for e in events if e.get("state") == "completed"]
    problems = []
    if not starts:
        problems.append("missing_started_event")
    if any(e.get("max_jobs_per_cycle") != 4 or e.get("batch_protocol") != "msw.seo.batch.v1" for e in starts):
        problems.append("unexpected_runtime_protocol")
    if len(admits) != len(finishes):
        problems.append("admission_finish_count_mismatch")
    admitted_jobs = 0
    for e in admits:
        if type(e.get("jobs")) is not int or not 1 <= e["jobs"] <= 4 or e.get("protocol") not in ("msw.seo.batch.v1", "legacy_single"):
            problems.append("invalid_batch_admission")
        else:
            admitted_jobs += e["jobs"]
    finished_jobs = confirmed_jobs = claims = 0
    for e in finishes:
        a, c, f, q = (e.get(k) for k in ("admitted", "completed", "failed", "claim_requests"))
        if any(type(v) is not int for v in (a, c, f, q)) or not 1 <= a <= 4 or c < 0 or f < 0 or a != c+f or q != 1:
            problems.append("invalid_batch_finish")
            continue
        finished_jobs += a
        confirmed_jobs += c
        claims += q
        if f:
            problems.append("batch_reports_failures")
    identifiers = [e.get("job_id") for e in completions]
    if len(identifiers) != len(set(identifiers)):
        problems.append("duplicate_completion_ids")
    if any(e.get("server_ack_verified") is not True for e in completions):
        problems.append("unverified_completion_ack")
    if admitted_jobs != finished_jobs or confirmed_jobs != len(completions):
        problems.append("aggregate_job_count_mismatch")
    blocked = sum(names.get(k, 0) for k in ("protocol_blocked", "authentication_blocked", "backoff_storage_blocked"))
    if blocked:
        problems.append("blocking_error_present")
    avoided = max(0, admitted_jobs - claims)
    return {
        "events": len(events), "runtime_starts": len(starts), "batches_admitted": len(admits),
        "batches_finished": len(finishes), "jobs_admitted": admitted_jobs,
        "jobs_finished": finished_jobs, "jobs_completed": confirmed_jobs,
        "completion_logs": len(completions), "unique_completion_ids": len(set(identifiers)),
        "completed_with_verified_ack": sum(e.get("server_ack_verified") is True for e in completions),
        "logged_claim_requests": claims, "legacy_single_claim_baseline_model": admitted_jobs,
        "claim_savings_model": avoided,
        "claim_savings_pct_model": round(100 * avoided / admitted_jobs, 2) if admitted_jobs else 0,
        "origin_pressure_events": names.get("origin_pressure", 0),
        "cpu_reduction_measured": False,
        "warnings": sorted(set(problems)),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log_export", type=Path, help="Exported Render JSONL or JSON log records")
    ap.add_argument("--strict", action="store_true", help="Nonzero exit when gaps or anomalies are found")
    args = ap.parse_args()
    report = audit(read_records(args.log_export))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if args.strict and report["warnings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
