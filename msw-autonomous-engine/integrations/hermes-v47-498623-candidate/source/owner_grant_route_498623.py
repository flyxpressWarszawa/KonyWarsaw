"""Strict route from the source-pinned owner CLI to the 498623 grant issuer.

The current V46 owner does not expose this task-scoped snapshot/fence API.
A V47 owner patch must implement it from the real owner DB, queue, STOP,
lease, native-effect and source-profile state. Missing APIs fail closed.
"""
import copy

import profile_498623 as p
from owner_grant_issue_498623 import issue_initial_grant


def issue_from_owner(owner):
    required = ("owner_lock", "active_engine", "active_sources",
                "snapshot_498623", "assert_498623_fences",
                "read_498623_admission", "read_498623_current_run",
                "append_498623_initial_grant", "read_498623_initial_grant")
    p.require(owner is not None
              and all(callable(getattr(owner, name, None)) for name in required),
              "v47_owner_grant_route_not_wired")

    # Capture exact owner state under the existing lock. The issuer reacquires
    # the same lock and the fence callback compares this state before and after
    # the create-only grant operation, closing the observation/append race.
    with owner.owner_lock():
        engine = copy.deepcopy(owner.active_engine())
        sources = copy.deepcopy(owner.active_sources())
        snapshot = owner.snapshot_498623()
        p.require(isinstance(snapshot, dict) and set(snapshot) == {"queue", "job"},
                  "v47_owner_snapshot_shape")
        p.validate_snapshot(snapshot["queue"], snapshot["job"])
        owner.assert_498623_fences(root_job_id=p.ROOT, task_id=p.TASK)
        snapshot = copy.deepcopy(snapshot)

    def check_fences():
        p.require(p.digest(owner.active_engine()) == p.digest(engine)
                  and p.digest(owner.active_sources()) == p.digest(sources),
                  "v47_owner_source_changed")
        current = owner.snapshot_498623()
        p.require(isinstance(current, dict) and set(current) == {"queue", "job"}
                  and p.digest(current) == p.digest(snapshot),
                  "v47_owner_snapshot_changed")
        p.validate_snapshot(current["queue"], current["job"])
        owner.assert_498623_fences(root_job_id=p.ROOT, task_id=p.TASK)

    return issue_initial_grant(owner=owner, check_fences=check_fences)
