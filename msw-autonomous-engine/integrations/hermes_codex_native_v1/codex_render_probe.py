"""Read-only Codex CLI readiness probe for a Hermes Render worker.

No credentials are printed, no task is claimed, and no Codex execution is attempted.
Run on the *actual* worker before enabling dispatch. A successful probe is not
proof of authentication or an end-to-end task acknowledgement.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys


def probe(which=shutil.which, run=subprocess.run):
    executable = which("codex")
    result = {"codex_cli_installed": bool(executable),
              "codex_cli_version_available": False,
              "codex_auth_verified": False,
              "production_dispatch_allowed": False}
    if not executable:
        result["reason"] = "codex_cli_not_installed"
        return result
    try:
        completed = run([executable, "--version"], capture_output=True, text=True,
                        timeout=8, check=False)
        result["codex_cli_version_available"] = completed.returncode == 0
        result["reason"] = ("authentication_and_e2e_not_verified"
                            if completed.returncode == 0 else "codex_cli_version_failed")
    except (OSError, subprocess.TimeoutExpired):
        result["reason"] = "codex_cli_probe_failed"
    return result


if __name__ == "__main__":
    print(json.dumps(probe(), sort_keys=True))
    sys.exit(0)
