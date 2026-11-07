"""Default isolated probe, with an explicit opt-in to a limited SEO worker.

Standby does not contact WordPress or claim jobs. The opt-in auxiliary mode
is not a full replacement for the existing central worker.
"""
from __future__ import annotations

import json
import os
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
    # The isolated probe remains the default until the explicitly scoped
    # WordPress node and this service are both configured.
    if os.environ.get("MSW_AUX_WORKER_ENABLED") == "1":
        from .render_auxiliary import main as run_auxiliary
        return run_auxiliary()
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
