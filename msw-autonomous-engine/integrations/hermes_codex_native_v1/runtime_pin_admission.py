"""Fail-closed compatibility admission for the existing Hermes standby overlay.

The pinned overlay can remain running, but a new Codex route is never enabled
when the public checkout differs from the exact version the overlay tested.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def admission(runtime_log: dict) -> dict:
    overlay = runtime_log.get("staging_overlay_launcher", runtime_log)
    if not isinstance(overlay, dict):
        return {"allowed": False, "reason": "invalid_runtime_evidence"}
    public_head = overlay.get("public_head")
    tested_head = overlay.get("tested_public_head")
    if not all(isinstance(x, str) and len(x) == 40 for x in (public_head, tested_head)):
        return {"allowed": False, "reason": "missing_runtime_pins"}
    if public_head != tested_head or overlay.get("public_head_matches_tested") is not True:
        return {"allowed": False, "reason": "runtime_pin_mismatch",
                "public_head": public_head, "tested_head": tested_head}
    if overlay.get("environment_modified") is True:
        return {"allowed": False, "reason": "environment_modified"}
    return {"allowed": False, "reason": "codex_auth_and_native_ack_not_verified",
            "public_head": public_head}


def main() -> int:
    import sys
    try:
        obj = json.load(sys.stdin)
    except (ValueError, TypeError):
        obj = {}
    result = admission(obj)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["allowed"] else 78


if __name__ == "__main__":
    raise SystemExit(main())
