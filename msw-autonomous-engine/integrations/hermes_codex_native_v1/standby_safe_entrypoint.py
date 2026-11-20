"""Safe entrypoint for the existing standby worker.

Preserves the existing overlay launcher and never enables Codex dispatch implicitly.
This entrypoint can replace the Render startCommand only after the existing overlay
and disk mount have been confirmed on the running instance.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

OVERLAY = Path("/tmp/msw-aux-seo/hermes-chat-staging/overlay-v7-repinned-a4dea/overlay_launcher.py")


def main() -> int:
    # A missing persistent overlay is a deployment mismatch, not a reason to
    # start a second worker or silently switch to a different queue backend.
    if not OVERLAY.is_file():
        print("HOLD: standby_overlay_missing; no worker started", file=sys.stderr)
        return 78
    # Never infer Codex authentication from a ChatGPT subscription or CLI presence.
    env = os.environ.copy()
    env["MSW_CODEX_PRODUCTION_DISPATCH"] = "0"
    # Keep the existing queue and startup behavior; no duplicate process.
    return subprocess.call([sys.executable, "-u", str(OVERLAY)], env=env)


if __name__ == "__main__":
    raise SystemExit(main())
