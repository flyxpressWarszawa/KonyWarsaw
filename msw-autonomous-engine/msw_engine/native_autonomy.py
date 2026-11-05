"""Lease-controlled orchestration of reviewed WordPress cron action groups."""
from __future__ import annotations

import re
import time
from pathlib import Path


def execute(payload: dict, data_dir: Path) -> dict:
    from .tasks import TaskError
    from .federation import gateway
    if (set(payload) != {"schema", "execution_id"} or payload.get("schema") != "msw.native-autonomy.job.v1"
            or not isinstance(payload.get("execution_id"), str)
            or re.fullmatch(r"[a-f0-9]{64}", payload["execution_id"]) is None):
        raise TaskError("native_autonomy_arguments")
    deadline = time.monotonic() + 150
    while True:
        result = gateway("autonomy_execute", {"execution_id": payload["execution_id"]})
        if result.get("state") == "running_or_uncertain":
            if time.monotonic() >= deadline:
                raise TaskError("native_autonomy_uncertain_no_repeat")
            time.sleep(5)
            continue
        if (result.get("schema") != "msw.native-autonomy.receipt.v1"
                or result.get("state") != "completed"
                or result.get("execution_id") != payload["execution_id"]
                or result.get("native_callbacks_executed") is not True
                or result.get("storage_readback_verified") is not True):
            raise TaskError("native_autonomy_receipt_invalid")
        return result
