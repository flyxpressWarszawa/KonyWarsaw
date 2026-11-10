"""Private owner-authorized portal knowledge, never committed to source control."""
import hashlib
import json
import os
from pathlib import Path
import re


def seed(directory):
    count = int(os.environ.get('MSW_BUSINESS_KNOWLEDGE_CHUNKS', '0'))
    digest = os.environ.get('MSW_BUSINESS_KNOWLEDGE_SHA256', '')
    if count == 0 and not digest:
        return {'state': 'not_configured'}
    if not 0 <= count <= 20:
        raise ValueError('business_knowledge_chunks')
    if not re.fullmatch(r'[a-f0-9]{64}', digest):
        if not count:
            return {'state': 'blocked', 'error': 'business_knowledge_pin_invalid'}
        raise ValueError('business_knowledge_digest')
    root = Path(directory) / 'owner-continuity'
    if root.is_symlink():
        raise ValueError('business_knowledge_symlink')
    path = root / ('business-knowledge-' + digest + '.json')
    if count:
        raw = ''.join(os.environ.get('MSW_BUSINESS_KNOWLEDGE_%02d' % i, '') for i in range(count))
        source = 'environment_migrated_to_disk'
    else:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1000000 or path.stat().st_mode & 0o077:
            return {'state': 'blocked', 'error': 'business_knowledge_disk_missing'}
        try:
            raw = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            return {'state': 'blocked', 'error': 'business_knowledge_disk_unreadable'}
        source = 'verified_private_disk'
    if len(raw.encode()) > 1000000 or hashlib.sha256(raw.encode()).hexdigest() != digest:
        if not count:
            return {'state': 'blocked', 'error': 'business_knowledge_digest'}
        raise ValueError('business_knowledge_digest')
    data = json.loads(raw)
    if (not isinstance(data, dict) or data.get('schema') != 'msw.business-knowledge.v1'
            or not isinstance(data.get('records'), list) or not 1 <= len(data['records']) <= 1000):
        raise ValueError('business_knowledge_schema')
    from .tasks import knowledge_ingest
    records = data['records']
    verified = 0
    for offset in range(0, len(records), 100):
        result = knowledge_ingest({'records': records[offset:offset + 100]}, directory)
        verified += result['readback_verified_records']
    if verified != len(records):
        raise ValueError('business_knowledge_readback')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink():
        raise ValueError('business_knowledge_symlink')
    if not path.exists():
        import tempfile
        import os as _os
        fd, temporary = tempfile.mkstemp(prefix='.business-knowledge-', dir=root)
        try:
            _os.fchmod(fd, 0o600)
            with _os.fdopen(fd, 'wb') as stream:
                stream.write(raw.encode())
                stream.flush()
                _os.fsync(stream.fileno())
            fd = -1
            _os.link(temporary, path)
        finally:
            if fd >= 0:
                _os.close(fd)
            if _os.path.exists(temporary):
                _os.unlink(temporary)
    if path.is_symlink() or path.stat().st_mode & 0o077 or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('business_knowledge_file_readback')
    return {'state': 'persisted_verified', 'records': verified, 'sha256': digest,
            'source': source, 'credentials_included': False,
            'coverage': 'observed_plugins_and_available_owner_portal_context_not_entire_account_history'}
