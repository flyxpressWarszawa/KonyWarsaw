"""Read-only WordPress → Render offload triage, not a task dispatcher.

Classifies queue inventory from an authenticated read-only SQL query. It never
claims jobs, modifies WordPress, executes callbacks, starts a Render process,
requests AI, or grants production authority.
"""
from __future__ import annotations

import json
import re
import sys

# Existing Render handlers are not evidence that a specific production worker
# has deployed them or that its current HMAC grant permits execution.
KINDS = {
    "seo.analyze": "pure_compute_candidate",
    "research.rank": "payload_dependent_candidate",  # business_hunter path can perform I/O
    "knowledge.ingest": "render_store_write_review",
    "knowledge.search": "render_store_read_review",  # sqlite3 FTS corpus readback required
    "runtime.catalog": "mixed_remote_read_review",
    "supreme.observe": "private_pack_and_store_review",
    "combo.analytics": "source_specific_review",
    "engine.v3.ingest": "source_specific_review",
    "module.execute": "native_wordpress_effect_hold",
    "llm.generate": "paid_provider_hold",
    "legacy.discovery.topics": "private_source_review",
    "legacy.dataset.prepare": "private_source_review",
    "legacy.llm80.train": "training_effect_hold",
    "quantum.schedule": "module_specific_review",
    "quantum.simulate": "module_specific_review",
    "fotonika.qnn": "module_specific_review",
}

# This is an offload *eligibility* classification, not live installation proof.
PURE_CANDIDATES = frozenset({"seo.analyze"})


def _number(value: object) -> int:
    if type(value) is int and 0 <= value <= 10**9:
        return value
    if isinstance(value, str) and re.fullmatch(r"(?:0|[1-9][0-9]{0,9})", value):
        numeric = int(value)
        if numeric <= 10**9:
            return numeric
    raise ValueError("invalid_count")


def triage(rows: object) -> dict:
    """Accept only {state,kind,jobs} rows. Never process payload/customer data."""
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("invalid_queue_snapshot")
    grouped: dict[tuple[str, str], int] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"state", "kind", "jobs"}:
            raise ValueError("invalid_row_contract")
        state, kind = row["state"], row["kind"]
        if state not in {"pending", "running"} or not isinstance(kind, str):
            raise ValueError("invalid_job_identity")
        if not re.fullmatch(r"[a-z0-9_.]{1,64}", kind):
            raise ValueError("invalid_job_identity")
        key = (state, kind)
        if key in grouped:
            raise ValueError("duplicate_snapshot_group")
        grouped[key] = _number(row["jobs"])

    inventory = []
    for (state, kind), count in sorted(grouped.items()):
        category = KINDS.get(kind, "unknown_kind_hold")
        inventory.append({
            "state": state, "kind": kind, "count": count,
            "category": category,
            "pure_candidate": kind in PURE_CANDIDATES,
            "on_render_verified": False,
            "effect_completed_verified": False,
        })
    pending = sum(count for (state, _), count in grouped.items() if state == "pending")
    native_pending = grouped.get(("pending", "module.execute"), 0)
    return {
        "schema": "msw.wp-render.offload-triage.v1",
        "state": "READONLY_CLASSIFICATION",
        "snapshot_pending": pending,
        "pending_native_php_effects": native_pending,
        "pending_pure_candidates": sum(
            count for (state, kind), count in grouped.items()
            if state == "pending" and kind in PURE_CANDIDATES
        ),
        "entries": inventory,
        "existing_worker_source_hash_verified": False,
        "signed_stop_hold_epoch_verified": False,
        "backup_and_restore_verified": False,
        "authenticated_result_ack_verified": False,
        "permission_to_deploy": False,
        "production_offload_completed": False,
        "actions_executed": 0,
        "note": "Queue states and worker presence never establish an offloaded business effect.",
    }


def main() -> int:
    """Bounded local parsing; no sockets, no WordPress or Render connection."""
    try:
        data = sys.stdin.read(16385)
        if len(data.encode("utf-8")) > 16384:
            raise ValueError("input_too_large")
        value = json.loads(data)
        result = triage(value)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (UnicodeError, ValueError, TypeError):
        print(json.dumps({
            "schema": "msw.wp-render.offload-triage.v1",
            "state": "HOLD",
            "code": "unverified_or_invalid_snapshot",
            "permission_to_deploy": False,
            "actions_executed": 0,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
