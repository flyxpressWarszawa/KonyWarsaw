"""Opt-in, one-shot staging executor for a *single* read-only Python HUB probe.

This is a bounded Hermes-side Python task proof. It does not message ChatGPT,
modify WordPress, dispatch arbitrary prompts or acknowledge on Hermes's behalf.
It does not execute any other task, even if the private queue contains one.
"""
from __future__ import annotations

import hashlib
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .github_chat_queue import GitHubChatQueue, SCHEMA, ID_RE, _request_sha

HUB_STATUS = "https://mobilny-serwis-warszawa.pl/wp-json/msw-hub/v1/status"
EXACT_GOAL = "MSW_PYTHON_HUB_READONLY_STATUS_V1"
RECEIPT_SCHEMA = "msw.hermes.python-hub.readonly.v1"
MAX_BODY = 16_384


class HealthReadError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_hub_status() -> dict:
    """Read a fixed public status endpoint, never a job-supplied URL or secret."""
    request = Request(HUB_STATUS, headers={"Accept": "application/json", "User-Agent": "MSW-Python-Hermes-Staging/1"})
    opener = build_opener(HTTPSHandler(), _NoRedirect())
    try:
        with opener.open(request, timeout=8) as response:
            raw = response.read(MAX_BODY + 1)
            if response.status != 200:
                raise HealthReadError("hub_http_not_200")
    except (HTTPError, URLError, TimeoutError, OSError):
        raise HealthReadError("hub_transport_unavailable") from None
    if len(raw) > MAX_BODY:
        raise HealthReadError("hub_response_too_large")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise HealthReadError("hub_json_invalid") from None
    if not isinstance(body, dict) or not any(key in body for key in ("status", "state", "ok", "version")):
        raise HealthReadError("hub_status_contract_invalid")
    return {"verified_public_http_200": True, "body_sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw), "source": HUB_STATUS}


def run_exact_readonly_task(queue: GitHubChatQueue, *, task_id: str, conversation_id: str,
                            reader=read_hub_status) -> dict:
    """Claim only the precisely identified owner-approved probe; report, never ACK.

    On a transport uncertainty, do not reissue the claim or report. The existing
    lease stays in storage and requires manual inspection to prevent duplicates.
    """
    if not (isinstance(task_id, str) and ID_RE.fullmatch(task_id)
            and isinstance(conversation_id, str) and ID_RE.fullmatch(conversation_id)):
        return {"state": "invalid_staging_binding", "reported": False}
    doc, _ = queue._read_queue()
    if doc.get("channel", {}).get("conversation_id") != conversation_id:
        return {"state": "conversation_mismatch", "reported": False}
    tasks = doc.get("tasks", [])
    if not tasks:
        return {"state": "empty", "reported": False}
    if len(tasks) != 1:
        return {"state": "unexpected_task_count", "reported": False}
    task = tasks[0]
    expected_sha = _request_sha(task_id, conversation_id, "review", EXACT_GOAL, None, False)
    if (task.get("state") != "pending" or task.get("task_id") != task_id
            or task.get("executor") != "hermes" or task.get("conversation_id") != conversation_id
            or task.get("kind") != "review" or task.get("goal") != EXACT_GOAL
            or task.get("parent_id") is not None or task.get("request_sha256") != expected_sha):
        return {"state": "task_not_allowlisted", "reported": False}

    claimed = queue.handle({"schema": SCHEMA, "operation": "claim", "executor": "hermes",
                            "conversation_key": conversation_id})
    if (claimed.get("state") != "claimed" or claimed.get("task_id") != task_id
            or claimed.get("request_sha256") != expected_sha or not claimed.get("lease_token")):
        return {"state": "claim_uncertain", "reported": False}

    try:
        evidence = reader()
        if (not isinstance(evidence, dict) or evidence.get("verified_public_http_200") is not True
                or not isinstance(evidence.get("body_sha256"), str)
                or len(evidence["body_sha256"]) != 64):
            raise HealthReadError("hub_probe_evidence_invalid")
        outcome = "completed"
        result_data = {"schema": RECEIPT_SCHEMA, "state": "public_hub_status_read",
                       "http_200": True, "source_sha256": evidence["body_sha256"],
                       "production_mutation_performed": False, "external_chat_verified": False,
                       "hermes_ack_verified": False}
    except HealthReadError as error:
        outcome = "blocked"
        code = str(error)
        if code not in {"hub_transport_unavailable", "hub_response_too_large", "hub_json_invalid",
                        "hub_status_contract_invalid", "hub_http_not_200", "hub_probe_evidence_invalid"}:
            code = "hub_probe_failed"
        result_data = {"schema": RECEIPT_SCHEMA, "state": "blocked", "code": code,
                       "production_mutation_performed": False, "external_chat_verified": False,
                       "hermes_ack_verified": False}
    result = json.dumps(result_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result_sha = hashlib.sha256(result.encode("utf-8")).hexdigest()
    reported = queue.handle({"schema": SCHEMA, "operation": "report", "task_id": task_id,
                             "lease_token": claimed["lease_token"], "result": result,
                             "result_sha256": result_sha, "outcome": outcome})
    if reported.get("state") != "reported" or reported.get("result_sha256") != result_sha:
        raise HealthReadError("report_write_uncertain")
    readback = queue.handle({"schema": SCHEMA, "operation": "status", "task_id": task_id})
    if readback.get("state") != "reported" or readback.get("result_sha256") != result_sha:
        raise HealthReadError("report_readback_uncertain")
    return {"state": "reported_waiting_for_hermes_ack", "outcome": outcome,
            "result_sha256": result_sha, "reported": True,
            "hermes_ack_verified": False, "external_chat_verified": False}


def main() -> int:
    # An explicit, exact one-shot opt-in. The long-running staging probe is unchanged.
    if os.environ.get("MSW_HERMES_STAGING_PYTHON_ONE_SHOT") != "1":
        print(json.dumps({"python_hub_one_shot": {"state": "disabled"}}), flush=True)
        return 0
    if (os.environ.get("MSW_HERMES_CHAT_BACKEND") != "github"
            or os.environ.get("MSW_HERMES_CHAT_LOOP_ENABLED") != "1"
            or os.environ.get("MSW_HERMES_PRODUCTION_MUTATION_ALLOWED") == "1"):
        print(json.dumps({"python_hub_one_shot": {"state": "policy_blocked"}}), flush=True)
        return 2
    try:
        result = run_exact_readonly_task(
            GitHubChatQueue(), task_id=os.environ.get("MSW_HERMES_STAGING_TASK_ID", ""),
            conversation_id=os.environ.get("MSW_HERMES_CONVERSATION_KEY", ""))
    except Exception:
        # No exception/HTTP response dumps: they could include private task data.
        print(json.dumps({"python_hub_one_shot": {"state": "uncertain_manual_review"}}), flush=True)
        return 2
    print(json.dumps({"python_hub_one_shot": result}, separators=(",", ":")), flush=True)
    return 0 if result.get("state") in {"empty", "reported_waiting_for_hermes_ack"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
