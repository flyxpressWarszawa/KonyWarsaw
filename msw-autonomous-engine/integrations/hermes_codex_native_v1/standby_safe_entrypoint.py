"""Safe entrypoint for the existing standby worker.

Preserves the existing overlay launcher and never enables Codex dispatch implicitly.
This entrypoint can replace the Render startCommand only after the existing overlay
and disk mount have been confirmed on the running instance.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

OVERLAY = Path("/tmp/msw-aux-seo/hermes-chat-staging/overlay-v7-repinned-a4dea/overlay_launcher.py")
OVERLAY_MODULES = {
    "msw_engine.github_chat_queue", "msw_engine.github_chat_receipt_audit",
    "msw_engine.github_queue_hermes_stage", "msw_engine.github_queue_staging",
    "msw_engine.wp_registry_cycle",
}


def preflight() -> dict | None:
    """Ask the existing launcher to verify its source pins without touching the queue."""
    try:
        checked = subprocess.run(
            [sys.executable, "-B", str(OVERLAY), "--verify-only"],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if checked.returncode or len(checked.stdout) > 16384:
            return None
        payload = json.loads(checked.stdout)
        report = payload["staging_overlay_verification"]
        selected = report["selected_modules"]
        if (payload.get("verify_only") is not True
                or payload.get("entrypoint_started") is not False
                or payload.get("verification_network_calls") != 0
                or report.get("version") != 7
                or report.get("public_modules_verified") != 10
                or report.get("overlay_modules_verified") != 5
                or report.get("environment_modified") is not False
                or not isinstance(selected, dict) or len(selected) != 11
                or not all(isinstance(item, dict) for item in selected.values())
                or {name for name, item in selected.items()
                    if item.get("source") == "overlay"} != OVERLAY_MODULES
                or any(item.get("source") not in ("public", "overlay")
                       for item in selected.values())
                or any(not isinstance(item.get("origin"), str)
                       or not item["origin"].startswith(
                           str(OVERLAY.parent / "overlay" / "msw_engine") + "/"
                           if item["source"] == "overlay"
                           else "/opt/render/project/src/msw-autonomous-engine/msw_engine/")
                       for item in selected.values())):
            return None
        return report
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return None


def main() -> int:
    # A missing persistent overlay is a deployment mismatch, not a reason to
    # start a second worker or silently switch to a different queue backend.
    if not OVERLAY.is_file():
        print("HOLD: standby_overlay_missing; no worker started", file=sys.stderr)
        return 78
    report = preflight()
    if report is None:
        print("HOLD: standby_overlay_preflight_failed; no worker started", file=sys.stderr)
        return 78
    # Never infer Codex authentication from a ChatGPT subscription or CLI presence.
    env = os.environ.copy()
    env["MSW_CODEX_PRODUCTION_DISPATCH"] = "0"
    print(json.dumps({"standby_entrypoint": {
        "transport_pins_verified": True,
        "public_head_matches_tested": report.get("public_head_matches_tested") is True,
        "codex_dispatch_allowed": False,
        "queue_backend": "existing_github_overlay",
    }}, sort_keys=True), flush=True)
    # Replace this process with the existing transport; never start two pollers.
    os.execve(sys.executable, [sys.executable, "-u", str(OVERLAY)], env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
