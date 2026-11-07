"""Low-rate SEO queue consumer for an independently registered WP node.

Only deterministic seo.analyze jobs are claimed. No scheduler, publication,
AI call, satellite operation, or private knowledge job is started here. The
local journal is ephemeral; an interrupted SEO calculation may be recomputed
when its server-side lease expires. It must not be used for other job kinds.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import threading
import time

from .client import Client, TransportError
from .runner import Worker, WorkerDraining

CAPABILITY = "seo.analyze"
POLL_SECONDS = 300
MAX_RETRY_SECONDS = 3600


class ProtocolError(Exception):
    """An unexpected queue response must stop this consumer's requests."""


def emit(state: str, **details) -> None:
    print(json.dumps({"render_auxiliary": {"state": state, "at": int(time.time()), **details}},
                     separators=(",", ":")), flush=True)


def retry_delay(failures: int, retry_after: int = 0) -> int:
    # One failed call causes at least five minutes of silence; repeated
    # failures exponentially increase the delay. The existing client bounds
    # Retry-After to 900 seconds.
    return max(retry_after, min(MAX_RETRY_SECONDS, POLL_SECONDS * 2 ** min(max(failures - 1, 0), 4)))


def step(client: Client, worker: Worker, stopping=lambda: False) -> dict:
    if stopping():
        raise WorkerDraining()
    worker.replay_receipts()
    if stopping():
        raise WorkerDraining()
    # Never send a different capability even if the node is misconfigured.
    response = client.send("claim", {"capabilities": [CAPABILITY]})
    if not isinstance(response, dict):
        raise ProtocolError("claim_response")
    if response.get("state") == "idle":
        return {"state": "idle"}
    job, lease = response.get("job"), response.get("lease_token")
    if (response.get("state") != "claimed" or not isinstance(job, dict)
            or job.get("kind") != CAPABILITY or not isinstance(lease, str) or not lease):
        raise ProtocolError("unexpected_claim")
    receipt = worker.execute(job, lease)
    if stopping():
        raise WorkerDraining()
    ack = worker.acknowledge(receipt)
    return {"state": ack["state"], "job_id": ack["job_id"],
            "server_ack_verified": ack["server_ack_verified"],
            "result_sha256": receipt["result_sha256"]}


def main() -> int:
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    # Restrict both handler discovery and auxiliary receipt hooks.
    os.environ["MSW_RUNTIME_MODE"] = "render-free"
    os.environ["MSW_AUTONOMOUS_AGENT"] = "0"
    directory = Path(os.environ.get("MSW_DATA_DIR", "/tmp/msw-aux-seo"))
    os.environ["MSW_DATA_DIR"] = str(directory)
    try:
        client = Client(os.environ.get("MSW_WP_URL", ""), os.environ.get("MSW_TRANSPORT_URL", ""),
                        os.environ.get("MSW_NODE", ""), os.environ.get("MSW_SECRET", ""))
        worker = Worker(client, directory, max_seconds=30, memory_mb=256, cancel_check=stop.is_set)
    except (ValueError, OSError) as error:
        emit("configuration_required", error_class=type(error).__name__, queue_consumption=False)
        stop.wait()
        return 0
    emit("started", capability=CAPABILITY, min_poll_seconds=POLL_SECONDS,
         scheduler_enabled=False, durable_storage=False)
    failures = 0
    while not stop.is_set():
        try:
            result = step(client, worker, stop.is_set)
            emit(result.pop("state"), **result)
            failures, delay = 0, POLL_SECONDS
        except WorkerDraining:
            break
        except ProtocolError as error:
            emit("protocol_blocked", code=str(error), queue_consumption=False)
            stop.wait()
            break
        except TransportError as error:
            failures += 1
            delay = retry_delay(failures, error.retry_after)
            if error.status in (401, 403):
                delay = MAX_RETRY_SECONDS
            emit("connection_backoff", code=error.code, http_status=error.status, retry_seconds=delay)
        except Exception as error:
            failures += 1
            delay = retry_delay(failures)
            emit("worker_backoff", error_class=type(error).__name__, retry_seconds=delay)
        stop.wait(delay)
    emit("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
