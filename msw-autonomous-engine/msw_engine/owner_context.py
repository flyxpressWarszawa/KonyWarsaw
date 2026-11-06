"""Operator-supplied private context, persisted outside the public source tree."""
import hashlib
import json
import os


def seed(directory):
    raw = os.environ.get('MSW_OWNER_CONTEXT_JSON', '')
    if not raw:
        return {'owner_context_state': 'not_configured'}
    sha = hashlib.sha256(raw.encode()).hexdigest()
    if len(raw.encode()) > 200000 or sha != os.environ.get('MSW_OWNER_CONTEXT_SHA256'):
        raise ValueError('owner_context_digest')
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('schema') != 'msw.owner-context.v1' or not isinstance(data.get('records'), list) or not 1 <= len(data['records']) <= 100:
        raise ValueError('owner_context_schema')
    records = []
    for record in data['records']:
        if not isinstance(record, dict) or set(record) != {'id', 'title', 'text', 'provenance'} or not all(isinstance(v, str) for v in record.values()):
            raise ValueError('owner_context_record')
        records.append({**record, 'approved': False, 'classification': 'owner_supplied_unverified',
                        'text_sha256': hashlib.sha256(record['text'].encode()).hexdigest()})
    from .tasks import knowledge_ingest, OWNER_CORPUS
    receipt = knowledge_ingest({'corpus': OWNER_CORPUS, 'owner_authorized': True, 'records': records}, directory)
    if receipt.get('readback_verified_records') != len(records):
        raise ValueError('owner_context_readback')
    root = directory / 'owner-continuity'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / ('owner-context-' + sha + '.json')
    if not path.exists():
        temporary = path.with_suffix('.tmp')
        temporary.write_text(raw)
        temporary.chmod(0o600)
        temporary.replace(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        raise ValueError('owner_context_file_readback')
    return {'owner_context_state': 'persisted_verified', 'owner_context_sha256': sha,
            'owner_context_records': len(records), 'private_context_readback_verified': True,
            'chatgpt_account_export_received': False}
