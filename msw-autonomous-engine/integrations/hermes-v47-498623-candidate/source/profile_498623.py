"""Fail-closed admission profile for the existing blocked task 498623.

This module is an offline candidate, not an ACK issuer or a queue consumer.
Callers must supply independently authenticated current owner and GitHub reads.
It deliberately cannot mutate production or execute a native request.
"""
import hashlib
import json
import math

ROOT = "msw-agent-v1:business:498623"
TASK = "1a6648ce-cc38-5ee3-a365-c1b358774b2b"
PARENT = "4522b34d-6898-50c6-babd-6d5b74a39c67"
WORK_KEY = "msw-agent-chat-v3:150a59f1b9fb83e1613e504643747101:2"
CONVERSATION = "6af7e699-24ec-83ed-b25a-3d6969794a84"
AUTOMATION = "Automation_03623231db8481918103b09295fc67c9"
REQUEST_SHA = "a72844b13166a410e0cc23ec5c421b36130b08176f321a56cd65ebe62e7aa376"
RESULT_SHA = "fb784718294b1192a2eb5f5aab996354febee8dcf9f08db21dd4ddc46a9ad581"
JOB_SHA = "e986977d0cdb4cac3537aec526ec8b74a34127855579721682dbeadcacb98daf"
ORIGINAL_TASK_SHA = "4cd716802ff41e2fa2075c5cc920ad805ca6dc5a8e474ec52a117089af65217a"
BINDING_SHA = "8043836154b26f855fb954ef2df3c0e6e03382c9a723163dfd7e7a2e05bc388b"
CI_RUN = 44842036582
CI_JOB = 136652630998
CI_HEAD = "daa51e7a516cb0edabe3665bbbbeb973b5bbc885"
TESTED_BLOBS = {"index.html": "a9e7eeb1bb368eaa2cf0d43e7ca6d7988c8a7a81",
                "app.css": "d51f0373a1c4216f85ad240377a51f6ff94923c9",
                "app.js": "c7f06e72edc5efdb7d7ec91b2256ae47612a85a4"}


class AdmissionError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise AdmissionError(reason)


def sha_text(value):
    require(isinstance(value, str), "text_required")
    try:
        return hashlib.sha256(value.encode("utf-8", "strict")).hexdigest()
    except UnicodeError as exc:
        raise AdmissionError("invalid_text") from exc


def digest(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise AdmissionError('invalid_digest_input') from exc
    return sha_text(raw)


def strict_json(value):
    def pairs(items):
        obj = {}
        for key, item in items:
            if key in obj:
                raise AdmissionError("duplicate_member")
            obj[key] = item
        return obj
    def constant(_):
        raise AdmissionError("nonfinite_number")
    def finite_float(raw):
        value = float(raw)
        require(math.isfinite(value), "nonfinite_number")
        return value
    require(isinstance(value, str) and len(value) <= 1000000, "invalid_document")
    try:
        result = json.loads(value, object_pairs_hook=pairs, parse_constant=constant, parse_float=finite_float)
        json.dumps(result, ensure_ascii=False).encode("utf-8", "strict")
        return result
    except (ValueError, TypeError, RecursionError, OverflowError, UnicodeError) as exc:
        raise AdmissionError("invalid_document") from exc


def validate_snapshot(queue, job):
    """Validate the unchanged original step and preceding native receipt.

    Exact job bytes are pinned. The caller must also check live locks, STOP,
    leases, effect histories and source pins under the owner boundary before
    performing *any* future write; this function alone authorizes nothing.
    """
    require(isinstance(queue, dict) and isinstance(job, dict), "object_required")
    channel = queue.get("channel")
    require(isinstance(channel, dict) and channel.get("automation_id") == AUTOMATION
            and channel.get("conversation_id") == CONVERSATION, "channel_mismatch")
    tasks = queue.get("tasks")
    require(isinstance(tasks, list) and len(tasks) == 1 and isinstance(tasks[0], dict), "queue_changed")
    task = tasks[0]
    require(digest(task) == ORIGINAL_TASK_SHA, "original_task_changed")
    identity = {"task_id": TASK, "parent_id": PARENT, "work_key": WORK_KEY,
                "conversation_id": CONVERSATION, "executor": "chatgpt_scheduled",
                "request_sha256": REQUEST_SHA, "result_sha256": RESULT_SHA,
                "state": "reported", "outcome": "blocked", "depth": 2}
    require(all(task.get(k) == v for k, v in identity.items())
            and type(task.get("depth")) is int and sha_text(task.get("result")) == RESULT_SHA,
            "task_mismatch")
    goal = strict_json(task.get("goal"))
    require(isinstance(goal, dict) and goal.get("root_job_id") == ROOT
            and goal.get("task_id") == TASK and goal.get("parent_id") == PARENT
            and goal.get("work_key") == WORK_KEY and goal.get("start_next") is False,
            "goal_mismatch")
    last_ack = queue.get("last_ack")
    require(isinstance(last_ack, dict) and last_ack.get("task_id") == PARENT
            and last_ack.get("outcome") == "completed", "parent_ack_mismatch")
    require(job.get("id") == ROOT and job.get("lane") == "business"
            and job.get("state") == "chat_held" and job.get("error") == "chat_blocked_no_ack"
            and type(job.get("attempts")) is int and job["attempts"] == 0
            and job.get("sha256") == JOB_SHA and sha_text(job.get("result")) == JOB_SHA,
            "job_mismatch")
    envelope = strict_json(job["result"])
    require(isinstance(envelope, dict) and envelope.get("root_job_id") == ROOT
            and type(envelope.get("step_index")) is int and envelope["step_index"] == 2
            and envelope.get("verification_scope") == "native_hermes", "envelope_mismatch")
    steps = envelope.get("steps")
    require(isinstance(steps, list) and len(steps) == 2 and all(isinstance(x, dict) for x in steps), "steps_mismatch")
    parent, step = steps
    verification = parent.get("native_verification")
    receipt = parent.get("native_receipt")
    require(parent.get("storage_ack") is True and isinstance(receipt, dict)
            and receipt.get("task_id") == PARENT and receipt.get("root_job_id") == ROOT
            and isinstance(verification, dict) and verification.get("verified") is True,
            "parent_native_unverified")
    held = step.get("noncompleted_report")
    require(isinstance(held, dict) and held.get("outcome") == "blocked"
            and held.get("result_sha256") == RESULT_SHA and held.get("request_binding_sha256") == BINDING_SHA
            and step.get("storage_ack") is False and step.get("chat_result") is None
            and "owner_supplement" not in step
            and all(step.get(k) is None for k in ("native_request", "native_receipt", "native_verification")),
            "step_effect_changed")
    return {"root_job_id": ROOT, "task_id": TASK, "original_result_sha256": RESULT_SHA,
            "request_binding_sha256": BINDING_SHA, "state": "snapshot_valid_only",
            "authorizes_ack": False, "authorizes_native_apply": False}


def validate_review_evidence(run, jobs, blobs):
    """Bind later test evidence to the changed source, never to original bytes.

    Inputs are observations from authenticated GitHub reads; this validator
    cannot establish their origin and must not be fed model-authored claims.
    """
    require(isinstance(run, dict) and type(run.get("id")) is int and run["id"] == CI_RUN
            and run.get("head_sha") == CI_HEAD and run.get("status") == "completed"
            and run.get("conclusion") == "success", "ci_run_mismatch")
    require(isinstance(jobs, list) and len(jobs) == 1 and isinstance(jobs[0], dict), "ci_job_mismatch")
    job = jobs[0]
    require(type(job.get("id")) is int and job["id"] == CI_JOB
            and type(job.get("run_id")) is int and job["run_id"] == CI_RUN
            and job.get("name") == "private-index" and job.get("status") == "completed"
            and job.get("conclusion") == "success", "ci_job_mismatch")
    checks = job.get("steps")
    require(isinstance(checks, list), "ci_steps_missing")
    matching = [x for x in checks if isinstance(x, dict) and x.get("name") ==
                "Verify HotelReady 90 HR01-HR13 in real Chromium with native 200 percent zoom"]
    require(len(matching) == 1 and matching[0].get("status") == "completed"
            and matching[0].get("conclusion") == "success", "browser_check_missing")
    require(blobs == TESTED_BLOBS, "source_blob_mismatch")
    return {"state": "later_revised_source_ci_evidence_validated", "run_id": CI_RUN,
            "head_sha": CI_HEAD, "source_blobs": dict(TESTED_BLOBS),
            "original_outcome": "blocked", "original_result_sha256": RESULT_SHA,
            "authorizes_ack": False, "authorizes_native_apply": False}
