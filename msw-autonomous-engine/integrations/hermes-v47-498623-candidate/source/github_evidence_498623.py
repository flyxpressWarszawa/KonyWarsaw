"""Authenticate the later 498623 browser-test evidence at the owner boundary.

Only fixed GitHub API endpoints, the existing owner token and immutable refs
are used. This is read-only and does not grant a report, ACK or native effect.
"""
import base64
import hashlib
import os
import urllib.error
import urllib.request

import profile_498623 as profile

BASE = 'https://api.github.com/repos/flyxpressWarszawa/signal-harbor-companion/'
DIR = 'knowledge/hermes-chat-loop/deliverables/498623-hotelready90-prototype/'
MAX_BYTES = 2_000_000


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise profile.AdmissionError('github_redirect_denied')


def _get(path, token, opener):
    allowed = {'actions/runs/' + str(profile.CI_RUN),
               'actions/runs/' + str(profile.CI_RUN) + '/jobs?per_page=100'}
    allowed.update('contents/' + DIR + name + '?ref=' + profile.CI_HEAD for name in profile.TESTED_BLOBS)
    profile.require(path in allowed, 'github_path_not_allowed')
    request = urllib.request.Request(BASE + path, method='GET', headers={
        'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
        'User-Agent': 'msw-498623-evidence-readonly-v1'})
    with opener.open(request, timeout=20) as response:
        profile.require(response.status == 200, 'github_status')
        body = response.read(MAX_BYTES + 1)
    profile.require(len(body) <= MAX_BYTES, 'github_response_too_large')
    try:
        return profile.strict_json(body.decode('utf-8', 'strict'))
    except UnicodeError as exc:
        raise profile.AdmissionError('github_invalid_utf8') from exc


def authenticated_evidence():
    token = os.environ.get('MSW_HERMES_GITHUB_TOKEN')
    profile.require(isinstance(token, str) and bool(token), 'owner_github_access_required')
    opener = urllib.request.build_opener(NoRedirect())
    run = _get('actions/runs/' + str(profile.CI_RUN), token, opener)
    jobs = _get('actions/runs/' + str(profile.CI_RUN) + '/jobs?per_page=100', token, opener)
    profile.require(isinstance(jobs, dict) and type(jobs.get('total_count')) is int
                    and jobs['total_count'] == 1 and isinstance(jobs.get('jobs'), list), 'github_job_count')
    blobs = {}
    for name in profile.TESTED_BLOBS:
        path = DIR + name
        value = _get('contents/' + path + '?ref=' + profile.CI_HEAD, token, opener)
        profile.require(isinstance(value, dict) and value.get('type') == 'file'
                        and value.get('path') == path and value.get('encoding') == 'base64'
                        and isinstance(value.get('content'), str) and type(value.get('size')) is int,
                        'github_source_binding')
        try:
            raw = base64.b64decode(''.join(value['content'].split()), validate=True)
        except (ValueError, TypeError) as exc:
            raise profile.AdmissionError('github_source_invalid_base64') from exc
        blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        profile.require(len(raw) == value['size'] and value.get('sha') == blob, 'github_blob_integrity')
        blobs[name] = blob
    return profile.validate_review_evidence(run, jobs['jobs'], blobs)
