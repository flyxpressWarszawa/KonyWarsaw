"""Build and import probe for a replacement Render worker.

This deliberately does not create a Client or Worker, access storage, contact
WordPress, claim queue jobs, or start any background scheduler. A separate,
reviewed migration is needed before this process can replace the live worker.
"""
from __future__ import annotations

import json
import signal
import threading
import time

from . import __version__
from .client import Client
from .runner import Worker


def emit(state: str) -> None:
    print(json.dumps({
        "render_standby": {
            "state": state,
            "at": int(time.time()),
            "worker_version": __version__,
            "core_imports_ok": Client is not None and Worker is not None,
            "queue_consumption": False,
            "external_connections": False,
            "persistent_state_migrated": False,
        }
    }, separators=(",", ":")), flush=True)


def main() -> int:
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    emit("ready_for_isolated_build_test")
    while not stop.wait(60):
        emit("standby")
    emit("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
