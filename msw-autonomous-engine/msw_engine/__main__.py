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


def startup_event(state: str, **details) -> None:
    """Write bounded startup diagnostics to Render stdout without secret values."""
    print(json.dumps({"startup": {"state": state, **details}}, separators=(",", ":")), flush=True)


def optional_startup(name: str, operation):
    """Run a nonessential startup task without taking down the queue worker."""
    startup_event(name + "_started")
    try:
        result = operation()
    except Exception as error:
        startup_event(name + "_unavailable", error_class=type(error).__name__)
        return None
    startup_event(name + "_completed")
    return result


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
        except (OSError, ValueError, KeyError, TypeError):
            return 1
    from .lifecycle import wait_before_start
    wait_before_start(directory)
    os.umask(0o077)
    factory_process = None
    try:
        startup_event("runtime_pack_install_started")
        pack = install(directory)
        print(json.dumps({"runtime_pack": pack.get("state", "unknown"), "modules": [item.get("id") for item in pack.get("modules", []) if isinstance(item, dict)]}), flush=True)
        startup_event("runtime_pack_install_completed")

        from .own_model import install as install_own_model
        own_model = optional_startup("own_model_install", lambda: install_own_model(directory))
        if own_model is not None:
            print(json.dumps({"own_model": own_model}), flush=True)

        from .business_hunter import checkpoint as hunter_checkpoint
        hunter = optional_startup("business_hunter_checkpoint", lambda: hunter_checkpoint(directory))
        if hunter is None:
            hunter = {"state": "checkpoint_unavailable", "runtime_version": "1.0.0"}
        print(json.dumps({"business_hunter": hunter}), flush=True)

        from .legacy_memory import restore
        legacy = optional_startup("legacy_memory_restore", lambda: restore(directory))
        if legacy is not None:
            print(json.dumps({"legacy_source_memory": legacy}), flush=True)

        base = os.environ.get("MSW_WP_URL", "").rstrip("/")
        endpoint = os.environ.get("MSW_TRANSPORT_URL", base + "/wp-content/plugins/msw-unified-engine/transport.php")
        startup_event("worker_configuration_started")
        client = Client(base, endpoint, os.environ.get("MSW_NODE", ""), os.environ.get("MSW_SECRET", ""))
        worker = Worker(client, directory, max_seconds=int(os.environ.get("MSW_MAX_JOB_SECONDS", "300")), memory_mb=int(os.environ.get("MSW_MAX_JOB_MEMORY_MB", "2048")))
        startup_event("worker_configuration_completed", once=args.once)

        # Optional isolated COMBO child. Its startup must not prevent the signed queue worker from running.
        if not args.once:
            from .combo_factory import start as start_combo_factory
            factory_process = optional_startup("combo_factory", lambda: start_combo_factory(Path(os.environ.get("MSW_COMBO_DATA_DIR", str(directory)))))
            if factory_process is not None:
                from .lifecycle import ChildProcess
                worker.track(ChildProcess(factory_process))
                print(json.dumps({"combo_factory": {"state": "process_started", "inference_verified": False}}), flush=True)

        # One narrowly scoped Alibaba credential test, opt-in only.
        # Run without delaying the main signed WordPress queue or leaking secret values.
        if not args.once and os.environ.get("MSW_ALIYUN_PROBE") == "1":
            import threading

            def aliyun_probe_task():
                try:
                    from .aliyun_probe import check
                    outcome = check(directory)
                except Exception as error:
                    outcome = {"schema": "msw.aliyun.probe.v1", "state": "unavailable",
                               "error_class": type(error).__name__}
                print(json.dumps({"aliyun_probe": outcome}, separators=(",", ":")), flush=True)

            probe_thread = threading.Thread(target=aliyun_probe_task, name="msw-aliyun-probe", daemon=True)
            probe_thread.start()
            worker.track(probe_thread)

        startup_event("worker_loop_started")
        return worker.loop(once=args.once, poke_cron_seconds=max(0, int(os.environ.get("MSW_WP_CRON_INTERVAL", "300"))))
    except (ValueError, OSError) as error:
        startup_event("configuration_required", error_class=type(error).__name__)
        print(json.dumps({"state": "configuration_required", "message": "Configure the HTTPS WordPress URL, registered node, scoped secret and writable private data directory."}), file=sys.stderr, flush=True)
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
