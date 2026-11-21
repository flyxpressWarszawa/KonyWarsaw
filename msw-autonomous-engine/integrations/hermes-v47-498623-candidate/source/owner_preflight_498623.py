"""Read-only 498623 preflight using the existing V46 owner boundary.

The real V46 owner module is injected by the host, never selected by the job,
request, environment, or model output. This candidate does not install a new
ledger, send an ACK or authorize a native effect.
"""
from contextlib import closing
import copy
from pathlib import Path
import sqlite3
import uuid

import github_evidence_498623
import profile_498623 as profile

JOBS = Path('/var/data/msw-engine/autonomous-agent/state.sqlite3')
ROUTER_DB = Path('/var/data/msw-hermes/msw-ai-registry/router.sqlite3')
NATIVE_DB = Path('/var/data/msw-hermes/home/state.db')
QUEUE = 'knowledge/hermes-chat-loop/github-queue.json'
POLICY = 'knowledge/hermes-chat-loop/executor-routing-policy.json'


def readonly(path):
    # Do not create a missing file or execute a journal checkpoint.
    path = Path(path)
    profile.require(path.is_file() and not path.is_symlink(), 'existing_native_database_required')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def native_absence(router_db=ROUTER_DB, native_db=NATIVE_DB):
    """Use the same bounded native/router history rules as the V46 runtime."""
    task = profile.TASK
    key = 'msw-native-apply-v3:' + task
    request_id = profile.ROOT + ':chat:2'
    markers = (task, key, request_id, profile.ROOT, profile.WORK_KEY)
    with closing(readonly(router_db)) as db:
        profile.require(db.execute('SELECT count(*) FROM job_fingerprints WHERE id=?', (key,)).fetchone()[0] == 0,
                        'existing_native_fingerprint')
        rows = db.execute('SELECT id,response,state,route FROM requests WHERE id=? OR response LIKE ? OR response LIKE ? OR response LIKE ? OR response LIKE ? ORDER BY id LIMIT 257',
                          (key, '%' + task + '%', '%' + request_id + '%',
                           '%' + profile.ROOT + '%', '%' + profile.WORK_KEY + '%')).fetchall()
    profile.require(len(rows) <= 256, 'router_history_bound')
    for row in rows:
        profile.require(row['id'] != key, 'existing_native_reservation')
        body = profile.strict_json(row['response'])
        profile.require(isinstance(body, dict) and row['id'] == request_id
                        and row['route'] == 'chatgpt_scheduled'
                        and row['state'] in {'CHAT_RESERVED', 'CHAT_WAITING', 'CHAT_RESULT_PENDING_DIGEST',
                                             'CHAT_RESULT_READY', 'CHAT_STORAGE_ACKED', 'CHAT_HELD'}
                        and body.get('schema') == 'msw.hermes.native-host-reservation.v3'
                        and body.get('scope') == 'router_request_bound_to_root_step'
                        and body.get('request_id') == request_id and body.get('root_job_id') == profile.ROOT
                        and body.get('task_id') == task and body.get('work_key') == profile.WORK_KEY
                        and body.get('assignment') == 'chatgpt_scheduled', 'existing_native_reservation')
    session = str(uuid.uuid5(uuid.NAMESPACE_URL, 'msw-native-root-v3:' + profile.ROOT))
    with closing(readonly(native_db)) as db:
        native_rows = db.execute('SELECT id,session_id,role,content FROM messages WHERE session_id=? OR content LIKE ? OR content LIKE ? OR content LIKE ? OR content LIKE ? ORDER BY id LIMIT 257',
                                 (session, '%' + task + '%', '%' + request_id + '%',
                                  '%' + profile.ROOT + '%', '%' + profile.WORK_KEY + '%')).fetchall()
    profile.require(len(native_rows) <= 256, 'native_history_bound')
    for row in native_rows:
        text = row['content'] or ''
        # A prior native effect can leave an assistant reply, a partial
        # request or an older-format message. Ignoring matching records just
        # because their role/prefix differs would falsely certify absence.
        if row['role'] != 'user' or not text.startswith('MSW_NATIVE_APPLICATION_V3\n'):
            profile.require(not any(marker in text for marker in markers),
                      'uncertain_prior_native_input')
            continue
        try:
            request = profile.strict_json(text.rsplit('\n', 1)[1])
        except (profile.AdmissionError, IndexError):
            profile.require(not any(marker in text for marker in markers), 'uncertain_prior_native_input')
            continue
        profile.require(isinstance(request, dict), 'invalid_native_request')
        profile.require(not (request.get('task_id') == task or request.get('request_id') == request_id
                             or request.get('idempotency_key') == key
                             or request.get('root_job_id') == profile.ROOT and request.get('step_index') == 2),
                        'existing_native_task_effect')
    return {'task_id': task, 'router_rows_examined': len(rows), 'native_rows_examined': len(native_rows),
            'native_session_id': session, 'database_writes': 0}


def snapshot(owner, jobs=JOBS):
    queue, queue_blob = owner.github_read(QUEUE)
    policy, policy_blob = owner.github_read(POLICY)
    binding = policy.get('executor_runtime_binding') if isinstance(policy, dict) else None
    profile.require(isinstance(binding, dict) and binding.get('schema') == 'msw.hermes.automation-runtime-binding.v3'
                    and binding.get('automation_id') == profile.AUTOMATION
                    and binding.get('logical_conversation_key') == profile.CONVERSATION, 'current_v3_policy_required')
    with closing(readonly(jobs)) as db:
        row = db.execute("SELECT * FROM jobs WHERE id=? AND lane='business'", (profile.ROOT,)).fetchone()
    profile.require(row is not None, 'original_job_required')
    job = dict(row)
    validation = profile.validate_snapshot(queue, job)
    return {'queue_blob': queue_blob, 'policy_blob': policy_blob, 'job_sha256': job['sha256'],
            'validation': validation}


def preflight(owner, jobs=JOBS, router_db=ROUTER_DB, native_db=NATIVE_DB):
    """Read twice while holding the *existing* owner's business lock.

    The host supplies the actual imported V46 owner. Result explicitly carries
    no permission to write. STOP/lease state, rollback and authentic CI inputs
    remain additional prerequisites for any eventual execution path.
    """
    # The completed run and commit are immutable. Read their authenticated
    # evidence before taking the business lock to avoid holding it across
    # several remote requests; live job/effect snapshots are checked inside.
    evidence = github_evidence_498623.authenticated_evidence()
    with owner.owner_lock():
        engine, sources = copy.deepcopy(owner.active_engine()), copy.deepcopy(owner.active_sources())
        before = snapshot(owner, jobs)
        absence = native_absence(router_db, native_db)
        profile.require(profile.digest(owner.active_engine()) == profile.digest(engine)
                        and profile.digest(owner.active_sources()) == profile.digest(sources)
                        and profile.digest(snapshot(owner, jobs)) == profile.digest(before),
                        'owner_preflight_changed')
    return {'state': 'READ_ONLY_PREFLIGHT', 'root_job_id': profile.ROOT, 'task_id': profile.TASK,
            'snapshot': before, 'native_absence': absence, 'later_test_evidence': evidence,
            'authorizes_ack': False, 'authorizes_native_apply': False,
            'database_writes': 0, 'queue_writes': 0, 'model_calls': 0}
