from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from .client import Client
from .runner import Worker
from .runtime_pack import install


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
    pack = install(directory)
    print(json.dumps({"runtime_pack": pack["state"], "modules": [item["id"] for item in pack["modules"]]}), flush=True)
    from .own_model import install as install_own_model
    own_model = install_own_model(directory)
    print(json.dumps({'own_model': own_model}), flush=True)
    from .business_hunter import checkpoint as hunter_checkpoint
    try:
        hunter = hunter_checkpoint(directory)
    except (OSError, ValueError, sqlite3.Error):
        hunter = {"state": "checkpoint_unavailable", "runtime_version": "1.0.0"}
    print(json.dumps({"business_hunter": hunter}), flush=True)
    from .legacy_memory import restore
    legacy = restore(directory)
    print(json.dumps({"legacy_source_memory": legacy}), flush=True)
    base = os.environ.get("MSW_WP_URL", "").rstrip("/")
    endpoint = os.environ.get("MSW_TRANSPORT_URL", base + "/wp-content/plugins/msw-unified-engine/transport.php")
    factory_process = None
    try:
        client = Client(base, endpoint, os.environ.get("MSW_NODE", ""), os.environ.get("MSW_SECRET", ""))
        worker = Worker(client, directory, max_seconds=int(os.environ.get("MSW_MAX_JOB_SECONDS", "300")), memory_mb=int(os.environ.get("MSW_MAX_JOB_MEMORY_MB", "2048")))
        # Optional isolated COMBO child: model loading never blocks existing work.
        # Separate persistent directory; no change to current receipts or credentials.
        if not args.once:
            from .combo_factory import start as start_combo_factory
            try:
                factory_process = start_combo_factory(Path(os.environ.get("MSW_COMBO_DATA_DIR", str(directory))))
                if factory_process is not None:
                    print(json.dumps({"combo_factory": {"state": "process_started", "inference_verified": False}}), flush=True)
            except OSError:
                print(json.dumps({"combo_factory": {"state": "process_start_failed", "inference_verified": False}}), flush=True)
        # Optional schedulers start once inside Worker.loop, including FotoAtlas.
        return worker.loop(once=args.once, poke_cron_seconds=max(0, int(os.environ.get("MSW_WP_CRON_INTERVAL", "300"))))
    except (ValueError, OSError):
        print(json.dumps({"state": "configuration_required", "message": "Configure the HTTPS WordPress URL, registered node, scoped secret and writable private data directory."}), file=sys.stderr)
        return 2
    finally:
        if factory_process is not None and factory_process.poll() is None:
            factory_process.terminate()
            try:
                factory_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                factory_process.kill()
                factory_process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
