"""Pure transition rules for a 498623 supplement; no production effects.

The owner is responsible for durable CAS, authentic readback, and locks.
This module cannot send ACK, change the queue, or start a model.
"""
import hashlib
import json

import profile_498623 as p


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def admit(task, evidence, report):
    p.require(isinstance(task, dict) and p.digest(task) == p.ORIGINAL_TASK_SHA,
              'original_task_changed')
    p.require(isinstance(evidence, dict) and evidence.get('state') == 'later_revised_source_ci_evidence_validated'
              and evidence.get('run_id') == p.CI_RUN and evidence.get('head_sha') == p.CI_HEAD
              and evidence.get('source_blobs') == p.TESTED_BLOBS
              and evidence.get('original_result_sha256') == p.RESULT_SHA, 'evidence_binding')
    p.require(isinstance(report, str) and 100 <= len(report.encode('utf-8', 'strict')) <= 16384,
              'report_length')
    return {'schema': 'msw.498623.supplement.v1', 'root_job_id': p.ROOT, 'task_id': p.TASK,
            'work_key': p.WORK_KEY, 'original_result_sha256': p.RESULT_SHA,
            'evidence_sha256': digest(evidence), 'report': report, 'report_sha256': p.sha_text(report),
            'phase': 'report_admitted'}


def next_action(phase, observed):
    """After intent has been committed, unknown results are read-only."""
    p.require(isinstance(observed, dict), 'observation_required')
    if phase == 'report_admitted':
        return 'persist_ack_intent'
    if phase == 'ack_uncertain':
        return 'read_and_verify_original_ack'
    if phase == 'ack_observed':
        return 'persist_native_intent'
    if phase == 'native_uncertain':
        return 'read_and_verify_native_receipt'
    if phase == 'native_observed':
        return 'verify_persisted_native_readback'
    raise p.AdmissionError('unknown_phase')
