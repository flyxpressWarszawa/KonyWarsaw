"""Owner-side one-shot grant issuer for the 498623 V47 candidate.

This module runs only inside the source-pinned owner's authenticated lock. It
has no filesystem path, queue access, model call, ACK writer, or native
dispatcher. Production integration must bind these methods to the owner ledger
and scheduler readback; absent methods fail closed.
"""
import copy
import re

import profile_498623 as p

ADMISSION_SCHEMA = "msw.owner-supplement.admission.v1"
RUN_SCHEMA = "msw.owner-supplement.current-run.v1"
GRANT_SCHEMA = "msw.supplement.initial-run-grant.v1"
GRANT_KEYS = {
    "schema", "operation_id", "root_job_id", "task_id",
    "admission_sha256", "run_id", "run_context",
}
_HEX = re.compile(r"^[a-f0-9]{64}$")


def _need(condition, reason):
    p.require(condition, reason)


def read_current_authority(*, read_admission, read_current_run):
    """Read authority independently of the saved grant, under the owner lock.

    Callbacks must read protected admission and the authenticated scheduler;
    neither may project authority from the initial-grant receipt itself.
    Copy observations immediately: a callback may reuse mutable objects.
    """
    _need(callable(read_admission) and callable(read_current_run),
          "owner_current_authority_not_wired")
    admission = copy.deepcopy(read_admission(root_job_id=p.ROOT, task_id=p.TASK))
    _need(isinstance(admission, dict)
          and set(admission) == {"schema", "root_job_id", "task_id",
                                 "operation_id", "admission_sha256", "state"},
          "owner_admission_shape")
    _need(admission["schema"] == ADMISSION_SCHEMA
          and admission["root_job_id"] == p.ROOT
          and admission["task_id"] == p.TASK
          and admission["state"] == "ADMITTED",
          "owner_admission_not_eligible")
    _need(isinstance(admission["operation_id"], str) and admission["operation_id"],
          "owner_admission_operation")
    _need(isinstance(admission["admission_sha256"], str)
          and _HEX.fullmatch(admission["admission_sha256"]), "owner_admission_hash")
    run = copy.deepcopy(read_current_run(operation_id=admission["operation_id"],
                                         root_job_id=p.ROOT, task_id=p.TASK))
    _need(isinstance(run, dict)
          and set(run) == {"schema", "operation_id", "root_job_id", "task_id",
                           "run_id", "run_context", "state"}, "owner_run_shape")
    _need(run["schema"] == RUN_SCHEMA
          and run["operation_id"] == admission["operation_id"]
          and run["root_job_id"] == p.ROOT and run["task_id"] == p.TASK
          and run["state"] == "CURRENT"
          and isinstance(run["run_id"], str) and run["run_id"]
          and isinstance(run["run_context"], dict), "owner_current_run_mismatch")
    return admission, run


def issue_initial_grant(*, owner, check_fences):
    """Append/read back one initial grant bound to live owner admission + run.

    owner must be the real owner module, already bound to protected business
    state. Its lock serializes admission, scheduler, and ledger observations.
    The create-only idempotent append/readback path supports crash recovery.
    """
    required = (
        "owner_lock", "read_498623_admission", "read_498623_current_run",
        "append_498623_initial_grant", "read_498623_initial_grant",
    )
    _need(owner is not None and all(callable(getattr(owner, name, None)) for name in required),
          "owner_grant_issuer_not_wired")
    _need(callable(check_fences), "owner_grant_fence_callback_required")

    with owner.owner_lock():
        check_fences()
        admission, run = read_current_authority(
            read_admission=owner.read_498623_admission,
            read_current_run=owner.read_498623_current_run)

        grant = {
            "schema": GRANT_SCHEMA,
            "operation_id": admission["operation_id"],
            "root_job_id": p.ROOT,
            "task_id": p.TASK,
            "admission_sha256": admission["admission_sha256"],
            "run_id": run["run_id"],
            "run_context": copy.deepcopy(run["run_context"]),
        }
        # The append must be create-only and digest-idempotent; conflicting
        # pre-existing grants must raise and never be replaced.
        owner.append_498623_initial_grant(copy.deepcopy(grant))
        recorded = copy.deepcopy(owner.read_498623_initial_grant(
            root_job_id=p.ROOT, task_id=p.TASK))
        check_fences()
        _need(p.digest(read_current_authority(
            read_admission=owner.read_498623_admission,
            read_current_run=owner.read_498623_current_run)) == p.digest((admission, run)),
            "owner_grant_authority_changed")

        _need(isinstance(recorded, dict)
              and set(recorded) == {"schema", "grant", "grant_sha256",
                                    "admission_sha256", "operation_id",
                                    "current_run_id", "current_run_context"},
              "owner_grant_readback_shape")
        _need(isinstance(recorded["grant_sha256"], str)
              and _HEX.fullmatch(recorded["grant_sha256"])
              and p.digest(grant) == recorded["grant_sha256"],
              "owner_grant_digest_mismatch")
        _need(recorded["schema"] == "msw.owner-supplement.initial-grant-readback.v1"
              and p.digest(recorded["grant"]) == p.digest(grant)
              and recorded["admission_sha256"] == admission["admission_sha256"]
              and recorded["operation_id"] == admission["operation_id"]
              and recorded["current_run_id"] == run["run_id"]
              and p.digest(recorded["current_run_context"]) == p.digest(run["run_context"]),
              "owner_grant_readback_mismatch")
        return copy.deepcopy(recorded)
