"""Opt-in, low-frequency Render lane for already-approved pure computations.

Reuses the existing signed WordPress lease and durable result readback. No
separate queue, admin credentials, plugin mutations or unsupervised cron work.
In particular, *module.execute* remains in the native WordPress lane.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .client import TransportError, encode


class PureOffloadLane:
    # These handlers compute on Render and never invoke native WP callbacks.
    CAPABILITIES = ("seo.analyze", "research.rank", "knowledge.search")
    SCHEMA = "msw.render.pure-offload.v1"

    def __init__(self, client, directory: Path, *, worker_factory=None, clock=time.time):
        if worker_factory is None:
            from .runner import Worker
            worker_factory = Worker
        self.client = client
        self.directory = Path(directory)
        self.clock = clock
        self.worker = worker_factory(client, self.directory, max_seconds=120, memory_mb=512)
        self.path = self.directory / "pure-offload-lane.json"

    def step(self, stopping=lambda: False) -> dict:
        if stopping():
            return {"state": "draining"}
        response = self.client.send("claim", {"capabilities": list(self.CAPABILITIES)})
        if not isinstance(response, dict):
            raise TransportError("pure_offload_response_schema")
        if response.get("state") == "idle":
            return {"state": "idle"}
        if response.get("state") != "claimed":
            raise TransportError("pure_offload_claim_state")
        job, lease = response.get("job"), response.get("lease_token")
        if not isinstance(job, dict) or not isinstance(lease, str) or not lease:
            raise TransportError("pure_offload_claim_schema")
        kind = job.get("kind")
        if kind not in self.CAPABILITIES:
            # Never execute arbitrary plugin callbacks, even if the origin errs.
            raise TransportError("pure_offload_capability_mismatch")
        # A shutdown after a lease is admitted leaves it for the server's
        # lease recovery; it does not attempt unsafely to release/replay it.
        if stopping():
            return {"state": "leased_deferred", "job_id": job.get("id"), "kind": kind}
        self.worker.cancel_check = stopping
        outcome = self.worker.acknowledge(self.worker.execute(job, lease))
        status = {
            "schema": self.SCHEMA,
            "at": int(self.clock()),
            "job_id": job.get("id"),
            "kind": kind,
            "state": outcome["state"],
            "server_ack_verified": outcome["server_ack_verified"] is True,
            "native_wordpress_callbacks_invoked": False,
            "business_effect_verified": False,
        }
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(encode(status))
        temporary.replace(self.path)
        return status

    def start(self, stopping) -> threading.Thread:
        def run():
            while not stopping():
                delay = 90
                try:
                    outcome = self.step(stopping)
                    print(json.dumps({"pure_offload_lane": outcome}, separators=(",", ":")), flush=True)
                    if outcome["state"] in {"completed", "failed"} and outcome.get("server_ack_verified"):
                        delay = 10
                    elif outcome["state"] in {"ack_pending", "leased_deferred"}:
                        delay = 180
                except TransportError as error:
                    delay = max(180, error.retry_after)
                    print(json.dumps({"pure_offload_lane": {"state": "retry", "code": error.code,
                                                               "http_status": error.status}}), flush=True)
                except Exception as error:
                    delay = 180
                    print(json.dumps({"pure_offload_lane": {"state": "retry", "code": type(error).__name__}}), flush=True)
                deadline = time.monotonic() + delay
                while not stopping() and time.monotonic() < deadline:
                    time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        thread = threading.Thread(target=run, name="msw-render-pure-offload", daemon=True)
        thread.start()
        return thread
