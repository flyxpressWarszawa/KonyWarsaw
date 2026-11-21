"""Live owner-lock-protected status of the 498623 one-shot intent journal.

No new admission, grant, ACK, effect, directory, file or queue is created.
Integrated into the existing source-pinned V46 owner CLI, once deployed.
"""
import copy
import os
from pathlib import Path

import durable_intent_498623 as journal
import profile_498623 as p

INTENT_PATH = Path("/var/data/msw-hermes/blocked-supplement/498623-v1/intent.sqlite3")


def status(owner, path=INTENT_PATH):
    path = Path(path)
    with owner.owner_lock():
        engine = copy.deepcopy(owner.active_engine())
        sources = copy.deepcopy(owner.active_sources())
        # A missing case must not become a new one through a status request.
        if os.path.lexists(path):
            recorded = journal.DurableIntent(path).read()
            result = {"state": recorded["phase"], "admission_sha256": recorded["admission_sha256"],
                      "original_result_sha256": recorded["original_result_sha256"],
                      "ack_sha256": recorded["ack_sha256"], "native_sha256": recorded["native_sha256"]}
        else:
            p.require(path.parent.is_dir() and not path.parent.is_symlink()
                      and path.parent.resolve(strict=True) == path.parent,
                      "existing_owner_case_parent_required")
            result = {"state": "NOT_ADMITTED", "admission_sha256": None,
                      "ack_sha256": None, "native_sha256": None,
                      "original_result_sha256": p.RESULT_SHA}
        p.require(p.digest(owner.active_engine()) == p.digest(engine)
                  and p.digest(owner.active_sources()) == p.digest(sources),
                  "owner_status_sources_changed")
    return {"schema": "msw.498623.owner-status.v1", "task_id": p.TASK,
            "root_job_id": p.ROOT, "journal": result,
            "queue_writes": 0, "database_writes": 0, "ack_writes": 0,
            "native_dispatches": 0, "model_calls": 0}
