"""Lease-controlled orchestration of reviewed WordPress cron action groups."""
from __future__ import annotations

import re
import time
from pathlib import Path


def execute(payload: dict, data_dir: Path) -> dict:
    from .tasks import TaskError
    from .federation import gateway
    if (set(payload) not in ({"schema", "execution_id"}, {"schema", "execution_id", "site_id"}) or payload.get("schema") != "msw.native-autonomy.job.v1"
            or not isinstance(payload.get("execution_id"), str)
            or re.fullmatch(r"[a-f0-9]{64}", payload["execution_id"]) is None):
        raise TaskError("native_autonomy_arguments")
    if "site_id" in payload and (not isinstance(payload["site_id"], str) or re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", payload["site_id"]) is None):
        raise TaskError("native_autonomy_site_id")
    request = {"execution_id": payload["execution_id"]}
    if "site_id" in payload:
        request["site_id"] = payload["site_id"]
    deadline = time.monotonic() + 150
    transport_failures = 0
    while True:
        try:
            result = gateway("autonomy_execute", request)
        except TaskError as error:
            # A late PHP/HTTP failure can occur after the native receipt was
            # committed. Ask for the SAME execution again while its lease is
            # live. WordPress returns the durable receipt, or its CAS guard
            # reports uncertainty; neither path repeats a started mutation.
            code = str(error)
            recoverable = code in {"federation_unavailable", "federation_response_invalid"} or bool(
                re.fullmatch(r"fed_autonomy_execute_http_50[0234](?::[A-Za-z0-9_.-]{1,100})?", code)
            )
            if not recoverable or transport_failures >= 2 or time.monotonic() >= deadline:
                raise
            transport_failures += 1
            time.sleep(2)
            continue
        if result.get("state") == "running_or_uncertain":
            if time.monotonic() >= deadline:
                raise TaskError("native_autonomy_uncertain_no_repeat")
            time.sleep(5)
            continue
        if (result.get("schema") != "msw.native-autonomy.receipt.v1"
                or result.get("state") != "completed"
                or result.get("execution_id") != payload["execution_id"]
                or result.get("native_callbacks_executed") is not True
                or result.get("storage_readback_verified") is not True
                or ("site_id" in payload and (result.get("site_id") != payload["site_id"]
                    or result.get("satellite_hmac_readback_verified") is not True))):
            raise TaskError("native_autonomy_receipt_invalid")
        return {**result, "receipt_transport_recovered": True} if transport_failures else result
