"""Authenticate the final jobs-CAS row in the existing V46 owner process.

A source-pinned owner supplies its existing business lock and engine/source
identity. No file is created, no state is changed and no ACK is dispatched.
The read-only entrypoint may be installed alongside existing V46 operations.
"""
from contextlib import closing
import copy
import os
from pathlib import Path

import durable_intent_498623 as journal
import final_job_guard_498623 as guard
import owner_preflight_498623 as preflight
import profile_498623 as p

JOBS = preflight.JOBS
INTENT = Path('/var/data/msw-hermes/blocked-supplement/498623-v1/intent.sqlite3')


def under_owner_lock(owner, jobs=JOBS, intent=INTENT):
    """The source-pinned caller MUST already hold the original business lock."""
    # Use immutable value snapshots; Python equality would accept False == 0
    # and 1 == 1.0, hiding a changed authenticated owner/source context.
    engine = copy.deepcopy(owner.active_engine())
    sources = copy.deepcopy(owner.active_sources())
    intent = Path(intent)
    if not os.path.lexists(intent):
        return {'state': 'NOT_ADMITTED', 'root_job_id': p.ROOT,
                'task_id': p.TASK, 'final_job_verified': False, 'database_writes': 0}
    state = journal.DurableIntent(intent).read()
    if state['phase'] != 'native_observed':
        return {'state': state['phase'], 'root_job_id': p.ROOT,
                'task_id': p.TASK, 'final_job_verified': False, 'database_writes': 0}
    with closing(preflight.readonly(jobs)) as db:
        rows = db.execute("SELECT * FROM jobs WHERE id=? AND lane='business'", (p.ROOT,)).fetchall()
    p.require(len(rows) == 1, 'final_job_row_required')
    row = dict(rows[0])
    if row.get('state') != 'completed':
        return {'state': 'AWAITING_FINAL_JOB', 'root_job_id': p.ROOT,
                'task_id': p.TASK, 'final_job_verified': False, 'database_writes': 0}
    envelope = p.strict_json(row.get('result'))
    result = guard.verify(row, envelope, state)
    with closing(preflight.readonly(jobs)) as db:
        second = db.execute("SELECT * FROM jobs WHERE id=? AND lane='business'", (p.ROOT,)).fetchone()
    p.require(second is not None and dict(second) == row
              and p.digest(owner.active_engine()) == p.digest(engine)
              and p.digest(owner.active_sources()) == p.digest(sources)
              and p.digest(journal.DurableIntent(intent).read()) == p.digest(state),
              'final_owner_snapshot_changed')
    return {**result, 'database_writes': 0, 'ack_writes': 0,
            'native_dispatches': 0, 'source_pins_verified': True}


def status(owner, jobs=JOBS, intent=INTENT):
    with owner.owner_lock():
        return under_owner_lock(owner, jobs=jobs, intent=intent)
