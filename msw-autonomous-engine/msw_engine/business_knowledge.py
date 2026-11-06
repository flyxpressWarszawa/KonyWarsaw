"""Private owner-authorized portal and plugin knowledge, independent of API keys."""
import hashlib
import json
import os


def seed(directory):
    count = int(os.environ.get('MSW_BUSINESS_KNOWLEDGE_CHUNKS', '0'))
    if count == 0: return {'state': 'not_configured'}
    if not 1 <= count <= 20: raise ValueError('business_knowledge_chunks')
    raw = ''.join(os.environ.get('MSW_BUSINESS_KNOWLEDGE_%02d' % i, '') for i in range(count))
    sha = hashlib.sha256(raw.encode()).hexdigest()
    if len(raw.encode()) > 1000000 or sha != os.environ.get('MSW_BUSINESS_KNOWLEDGE_SHA256'):
        raise ValueError('business_knowledge_digest')
    data = json.loads(raw)
    if data.get('schema') != 'msw.business-knowledge.v1' or not isinstance(data.get('records'), list) or not 1 <= len(data['records']) <= 1000:
        raise ValueError('business_knowledge_schema')
    from .tasks import knowledge_ingest
    records = data['records']; verified = 0
    for offset in range(0, len(records), 100):
        result = knowledge_ingest({'records': records[offset:offset + 100]}, directory)
        verified += result['readback_verified_records']
    if verified != len(records): raise ValueError('business_knowledge_readback')
    root = directory / 'owner-continuity'; root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / ('business-knowledge-' + sha + '.json')
    if not path.exists():
        temporary = path.with_suffix('.tmp'); temporary.write_text(raw); temporary.chmod(0o600); temporary.replace(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha: raise ValueError('business_knowledge_file_readback')
    return {'state': 'persisted_verified', 'records': verified, 'sha256': sha, 'credentials_included': False,
            'coverage': 'observed_plugins_and_available_owner_portal_context_not_entire_account_history'}
