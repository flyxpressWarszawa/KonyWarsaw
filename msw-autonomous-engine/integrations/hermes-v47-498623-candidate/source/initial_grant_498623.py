"""Fail-closed verifier for a task-scoped initial owner grant.

The active owner must create/store the grant under its existing lock and pass
the ledger's expected grant hash and admission hash here. This module never
creates a grant, accepts model-authored authority, or mutates production.
"""
import re

import profile_498623 as p

GRANT_SCHEMA = "msw.supplement.initial-run-grant.v1"
GRANT_KEYS = {
    "schema", "operation_id", "root_job_id", "task_id",
    "admission_sha256", "run_id", "run_context",
}
_HEX = re.compile(r"^[a-f0-9]{64}$")


def validate_initial_grant(
    grant,
    *,
    expected_grant_sha256,
    expected_admission_sha256,
    expected_operation_id,
    expected_run_id,
    expected_run_context,
):
    """Validate V46-compatible grant fields against trusted owner readbacks.

    The expected hashes/identity/context must come from the locked owner ledger
    and independently read current run. They must never come from the grant
    document or model output.
    """
    p.require(isinstance(grant, dict) and set(grant) == GRANT_KEYS,
              "initial_grant_shape")
    p.require(grant.get("schema") == GRANT_SCHEMA, "initial_grant_schema")
    p.require(grant.get("root_job_id") == p.ROOT
              and grant.get("task_id") == p.TASK, "initial_grant_task_binding")
    p.require(isinstance(expected_grant_sha256, str)
              and _HEX.fullmatch(expected_grant_sha256)
              and p.digest(grant) == expected_grant_sha256,
              "initial_grant_hash_mismatch")
    p.require(isinstance(expected_admission_sha256, str)
              and _HEX.fullmatch(expected_admission_sha256)
              and grant.get("admission_sha256") == expected_admission_sha256,
              "initial_grant_admission_mismatch")
    p.require(isinstance(expected_operation_id, str)
              and expected_operation_id
              and grant.get("operation_id") == expected_operation_id,
              "initial_grant_operation_mismatch")
    p.require(isinstance(expected_run_id, str)
              and expected_run_id
              and grant.get("run_id") == expected_run_id,
              "initial_grant_run_mismatch")
    p.require(isinstance(expected_run_context, dict)
              and isinstance(grant.get("run_context"), dict)
              and p.digest(grant["run_context"]) == p.digest(expected_run_context),
              "initial_grant_run_context_mismatch")
    return {
        "schema": "msw.owner-supplement.initial-grant-readback.v1",
        "root_job_id": p.ROOT,
        "task_id": p.TASK,
        "admission_sha256": expected_admission_sha256,
        "grant_sha256": expected_grant_sha256,
        "run_id": expected_run_id,
        "verified": True,
        "authorizes_ack": False,
        "authorizes_native_apply": False,
    }
