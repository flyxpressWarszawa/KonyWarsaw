"""Drain existing AI jobs independently of the large native-callback backlog.

No new schedule, queue, provider credentials or publication permissions are added.
Every job still requires a server lease and the existing durable ACK/readback.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .client import TransportError, encode


class PriorityLane:
    CAPABILITIES = ("llm.generate",)

    def __init__(self, client, directory: Path, *, worker_factory=None, clock=time.time):
        if worker_factory is None:
            from .runner import Worker
            worker_factory = Worker
        self.client = client
        self.directory = directory
        self.clock = clock
        self.worker = worker_factory(client, directory, max_seconds=300, memory_mb=768)
        self.path = directory / "editorial-ai-lane.json"

    def step(self, stopping=lambda: False) -> dict:
        if stopping():
            return {"state": "draining"}
        # The main worker recovers all pending receipts from the shared durable
        # journal. Avoid a second replay loop that would race those ACKs.
        response = self.client.send("claim", {"capabilities": list(self.CAPABILITIES)})
        if response.get("state") != "claimed":
            return {"state": "idle"}
        job, lease = response.get("job"), response.get("lease_token")
        if not isinstance(job, dict) or not isinstance(lease, str):
            raise TransportError("priority_lane_claim_schema")
        if job.get("kind") not in self.CAPABILITIES:
            # Do not execute a capability the server was not asked to lease.
            raise TransportError("priority_lane_capability_mismatch")
        self.worker.cancel_check = stopping
        result = self.worker.acknowledge(self.worker.execute(job, lease))
        status = {"schema": "msw.editorial-ai-lane.v1", "at": int(self.clock()),
                  "capabilities": list(self.CAPABILITIES), **result,
                  "publication_verified": False,
                  "measurement": "Existing AI queue job and server ACK; publication needs a separate WordPress receipt."}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(encode(status))
        temporary.replace(self.path)
        return status

    def start(self, stopping) -> threading.Thread:
        def run():
            delay = 10
            while not stopping():
                try:
                    status = self.step(stopping)
                    print(json.dumps({"editorial_ai_lane": status}), flush=True)
                    delay = 15 if status["state"] == "idle" else 5
                except Exception as error:
                    # No prompts, provider replies, tokens or customer content.
                    status = {"state": "retry", "code": type(error).__name__}
                    if isinstance(error, TransportError):
                        status.update(code=error.code, http_status=error.status)
                        delay = max(error.retry_after, min(300, max(30, delay * 2)))
                    else:
                        delay = min(300, max(30, delay * 2))
                    print(json.dumps({"editorial_ai_lane": status}), flush=True)
                deadline = time.monotonic() + delay
                while not stopping() and time.monotonic() < deadline:
                    time.sleep(min(1, max(0, deadline - time.monotonic())))
        thread = threading.Thread(target=run, name="msw-editorial-ai-lane", daemon=True)
        thread.start()
        return thread
