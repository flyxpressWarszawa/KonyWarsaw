"""Owner-lock adapter for the 498623 initial grant readback.

Callbacks must be supplied by the source-pinned owner while its business lock
is held. The receipt provides the grant hash; separate callbacks read current
admission and the authenticated scheduler, never the receipt's copied context.
"""
import copy
import profile_498623 as p
from initial_grant_498623 import validate_initial_grant
from owner_grant_issue_498623 import read_current_authority

READBACK_SCHEMA = "msw.owner-supplement.initial-grant-readback.v1"
READBACK_KEYS = {
    "schema", "grant", "grant_sha256", "admission_sha256",
    "operation_id", "current_run_id", "current_run_context",
}


def read_verified_grant(*, owner_readback, read_admission, read_current_run, check_fences):
    p.require(callable(owner_readback) and callable(check_fences),
              "owner_grant_callbacks_required")
    check_fences()
    admission, run = read_current_authority(
        read_admission=read_admission, read_current_run=read_current_run)
    raw = copy.deepcopy(owner_readback(root_job_id=p.ROOT, task_id=p.TASK))
    p.require(isinstance(raw, dict) and set(raw) == READBACK_KEYS,
              "owner_grant_readback_shape")
    p.require(raw.get("schema") == READBACK_SCHEMA,
              "owner_grant_readback_schema")
    p.require(raw["admission_sha256"] == admission["admission_sha256"]
              and raw["operation_id"] == admission["operation_id"]
              and raw["current_run_id"] == run["run_id"]
              and p.digest(raw["current_run_context"]) == p.digest(run["run_context"]),
              "owner_grant_stale_authority")
    verified = validate_initial_grant(
        copy.deepcopy(raw["grant"]),
        expected_grant_sha256=raw["grant_sha256"],
        expected_admission_sha256=admission["admission_sha256"],
        expected_operation_id=admission["operation_id"],
        expected_run_id=run["run_id"],
        expected_run_context=run["run_context"],
    )
    check_fences()
    p.require(p.digest(read_current_authority(read_admission=read_admission,
                                    read_current_run=read_current_run)) == p.digest((admission, run)),
              "owner_grant_authority_changed")
    return verified
