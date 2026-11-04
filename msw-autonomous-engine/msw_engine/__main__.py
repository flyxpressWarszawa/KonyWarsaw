from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

from .client import Client
from .runner import Worker


def main() -> int:
    parser = argparse.ArgumentParser(description="MSW external compute engine")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    directory = Path(os.environ.get("MSW_DATA_DIR", "/var/lib/msw-engine"))
    if args.health:
        try:
            data = json.loads((directory / "health.json").read_text())
            return 0 if time.time() - data["at"] < 600 and time.time() - data["last_server_contact"] < 900 else 1
        except (OSError, ValueError, KeyError):
            return 1
    os.umask(0o077)
    base = os.environ.get("MSW_WP_URL", "").rstrip("/")
    endpoint = os.environ.get("MSW_TRANSPORT_URL", base + "/wp-content/plugins/msw-unified-engine/transport.php")
    try:
        client = Client(base, endpoint, os.environ.get("MSW_NODE", ""), os.environ.get("MSW_SECRET", ""))
        worker = Worker(client, directory, max_seconds=int(os.environ.get("MSW_MAX_JOB_SECONDS", "300")), memory_mb=int(os.environ.get("MSW_MAX_JOB_MEMORY_MB", "2048")))
        return worker.loop(once=args.once, poke_cron_seconds=max(0, int(os.environ.get("MSW_WP_CRON_INTERVAL", "300"))))
    except (ValueError, OSError):
        print(json.dumps({"state": "configuration_required", "message": "Configure the HTTPS WordPress URL, registered node, scoped secret and writable private data directory."}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
