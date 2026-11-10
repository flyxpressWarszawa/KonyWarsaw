"""Read-only observer for MSW knowledge and Alibaba Token Plan usage.

Independent of production runtime. No model calls, background workers or key
storage. CLI query is explicitly opt-in and requires pre-existing authorization.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess

SCHEMA = "msw.observer.readonly.v1"
STANDARD_CREDITS_NOV_2026 = 80000


def quota(data, *, observed_at=None):
    base = {"state": "NOT_CONNECTED", "verified": False}
    if not isinstance(data, dict) or data.get("hasQuotaData") is not True:
        return base
    ratio, reset_ms = data.get("per1MonthPercentage"), data.get("per1MonthResetTime")
    if type(ratio) not in (float, int) or not math.isfinite(ratio) or not 0 <= ratio <= 1:
        return {**base, "state": "INVALID_QUOTA"}
    if type(reset_ms) not in (float, int) or not math.isfinite(reset_ms):
        return {**base, "state": "INVALID_QUOTA"}
    try:
        reset = datetime.fromtimestamp(reset_ms / 1000, tz=timezone.utc)
        now = (datetime.fromisoformat(observed_at.replace("Z", "+00:00")).astimezone(timezone.utc)
               if observed_at else datetime.now(timezone.utc))
    except (ValueError, OverflowError, OSError, AttributeError):
        return {**base, "state": "INVALID_QUOTA"}
    if not now < reset or (reset - now).days > 40:
        return {**base, "state": "STALE_OR_INVALID_QUOTA"}
    pct = round(100 * ratio, 2)
    level = "critical" if pct >= 90 else "high" if pct >= 80 else "caution" if pct >= 60 else "normal"
    return {"state": "VERIFIED_USAGE", "verified": True, "percent_used": pct,
            "percent_remaining": round(100 - pct, 2), "level": level,
            "reset_at_utc": reset.isoformat(), "observed_at_utc": now.isoformat(),
            "approx_used_credits_standard_2026_11": round(STANDARD_CREDITS_NOV_2026 * ratio),
            "estimated_not_provider_bill": True}


def _valid_receipts(data, id_field):
    if not isinstance(data, list):
        return []
    unique = {}
    for item in data:
        if (isinstance(item, dict) and isinstance(item.get(id_field), str)
                and 8 <= len(item[id_field]) <= 128):
            unique.setdefault(item[id_field], item)
    return list(unique.values())


def learning(data):
    if not isinstance(data, dict):
        return {"state": "NOT_MEASURED"}
    edits = set()
    for x in (data.get("editor_events") if isinstance(data.get("editor_events"), list) else []):
        if (isinstance(x, dict) and x.get("source") == "wordpress_editor_metadata"
                and type(x.get("post_id")) is int and isinstance(x.get("utc"), str)):
            edits.add((x["utc"], x["post_id"]))
    historic = data.get("historical_learning")
    proven = [x["records_reviewed"] for x in historic if
              isinstance(x, dict) and x.get("state") == "source_batch_reviewed"
              and x.get("storage_readback_verified") is True
              and type(x.get("records_reviewed")) is int and x["records_reviewed"] >= 0] if isinstance(historic, list) else []
    ingests = _valid_receipts(data.get("knowledge_receipts"), "receipt_id")
    ingests = [x for x in ingests if x.get("storage_readback_verified") is True
               and type(x.get("readback_verified_records")) is int
               and x["readback_verified_records"] >= 0]
    trains = _valid_receipts(data.get("training_receipts"), "receipt_id")
    trains = [x for x in trains if x.get("training_performed") is True
              and x.get("checkpoint_readback_verified") is True
              and type(x.get("steps_this_job")) is int and x["steps_this_job"] >= 0]
    quality = _valid_receipts(data.get("evaluations"), "evaluation_run_id")
    quality = [x for x in quality if x.get("independent_evaluation_verified") is True
               and x.get("quality_improvement_proven") is True]
    return {"state": "EVIDENCE_PARTIAL",
            "editor_events_observed": len(edits),
            "unique_posts_observed": len({x[1] for x in edits}),
            "edits_are_not_training": True,
            "historical_records_reviewed_cumulative": max(proven) if proven else None,
            "knowledge_verified_ingest_records": sum(x["readback_verified_records"] for x in ingests) if ingests else None,
            "verified_weight_training_runs": len(trains) if trains else None,
            "verified_training_steps_in_receipts": sum(x["steps_this_job"] for x in trains) if trains else None,
            "weights_modified_runs": sum(x.get("weights_modified") is True for x in trains) if trains else None,
            "independently_proven_quality_improvements": len(quality) if data.get("evaluations") else None}


def _load(path):
    text = Path(path).read_text(encoding="utf-8")
    if len(text) > 262144:
        raise ValueError("input_too_large")
    return json.loads(text)


def cli_quota_readonly():
    try:
        x = subprocess.run(["bl", "usage", "token-plan", "--output", "json"],
                           capture_output=True, text=True, timeout=20, check=False)
        return json.loads(x.stdout) if x.returncode == 0 and len(x.stdout) < 32768 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--quota-json")
    p.add_argument("--learning-json")
    p.add_argument("--read-quota-cli", action="store_true")
    p.add_argument("--observed-at")
    a = p.parse_args()
    if a.quota_json and a.read_quota_cli:
        p.error("Select only one quota source")
    try:
        q = cli_quota_readonly() if a.read_quota_cli else _load(a.quota_json) if a.quota_json else None
        l = _load(a.learning_json) if a.learning_json else None
        # Only aggregate counters are emitted; never echo raw provider or client data.
        print(json.dumps({"schema": SCHEMA, "alibaba": quota(q, observed_at=a.observed_at),
                          "learning": learning(l)}, ensure_ascii=False, sort_keys=True))
    except (ValueError, OSError, json.JSONDecodeError):
        print(json.dumps({"schema": SCHEMA, "state": "INVALID_INPUT"}))


if __name__ == "__main__":
    main()
