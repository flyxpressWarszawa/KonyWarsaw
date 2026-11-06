"""Private import of an explicitly owner-authorized account export.

The download grant is held only in service secrets. No browser/session cookie,
account password or arbitrary URL is accepted. This imports source data only.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zipfile import ZipFile

MAX_ARCHIVE = 512 * 1024 * 1024
MAX_JSON = 256 * 1024 * 1024


def trusted_url(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment or parsed.port not in (None,443)
            or not (host == 'chatgpt.com' or host.endswith('.chatgpt.com') or host == 'openai.com' or host.endswith('.openai.com') or host.endswith('.oaiusercontent.com'))):
        raise ValueError('export_official_https_url_required')
    return url


class ExportRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        trusted_url(url)
        return super().redirect_request(request, fp, code, message, headers, url)


def fingerprint(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def status(directory):
    try:
        data = json.loads((directory / 'chatgpt-export-status.json').read_text())
        return data if isinstance(data, dict) else {'state':'not_imported'}
    except (OSError, ValueError):
        return {'state':'not_imported'}


def import_zip(archive, directory, *, grant_sha256='local-owner-supplied'):
    from .historical_learning import scrub
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size > MAX_ARCHIVE:
        raise ValueError('export_archive_limit')
    sha = fingerprint(archive)
    root = directory / 'chatgpt-imports'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = root / (sha + '.sqlite3')
    temporary = root / (sha + '.tmp.sqlite3')
    if temporary.is_symlink() or target.is_symlink():
        raise ValueError('export_storage_link')
    with ZipFile(archive) as zipped:
        candidates = [item for item in zipped.infolist() if item.filename.replace('\\','/').split('/')[-1] == 'conversations.json']
        if len(candidates) != 1 or candidates[0].file_size > MAX_JSON:
            raise ValueError('export_conversations_json_missing_or_large')
        with zipped.open(candidates[0]) as stream:
            data = json.load(stream)
    if not isinstance(data, list) or len(data) > 200000:
        raise ValueError('export_conversation_schema')
    temporary.unlink(missing_ok=True)
    count = conversations = 0
    try:
        with sqlite3.connect(temporary) as db:
            db.execute('CREATE TABLE documents (id INTEGER PRIMARY KEY,title TEXT NOT NULL,body TEXT NOT NULL,source TEXT NOT NULL,kind TEXT NOT NULL,digest TEXT NOT NULL)')
            for conversation in data:
                if not isinstance(conversation, dict) or not isinstance(conversation.get('mapping'), dict):
                    raise ValueError('export_conversation_mapping')
                conversations += 1
                title = str(conversation.get('title') or 'Conversation')[:1000]
                for node_id, node in conversation['mapping'].items():
                    message = node.get('message') if isinstance(node, dict) else None
                    if not isinstance(message, dict):
                        continue
                    content = message.get('content')
                    if not isinstance(content, dict):
                        continue
                    parts = content.get('parts', [])
                    text_parts = [part if isinstance(part,str) else part.get('text','') if isinstance(part,dict) else '' for part in parts] if isinstance(parts,list) else []
                    body = scrub('\n'.join(text_parts))
                    if not body.strip():
                        continue
                    role = str((message.get('author') or {}).get('role','unknown'))
                    source = 'chatgpt-export:' + sha + ':' + str(conversation.get('id') or conversation.get('conversation_id') or conversations) + ':' + str(node_id)
                    count += 1
                    record_title = title + ' [' + role[:60] + ']'
                    digest = hashlib.sha256((record_title + '\0' + body).encode()).hexdigest()
                    db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?)', (count,record_title,body,source,'chatgpt_export_unreviewed',digest))
            if db.execute('SELECT COUNT(*) FROM documents').fetchone()[0] != count or db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('export_database_verification')
        with sqlite3.connect(temporary) as fresh:
            if fresh.execute('SELECT COUNT(*) FROM documents').fetchone()[0] != count:
                raise ValueError('export_record_readback')
        if count == 0:
            raise ValueError('export_empty_text')
        temporary.chmod(0o600)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    receipt = {'schema':'msw.chatgpt-export.import.v1','state':'imported_verified','archive_sha256':sha,
               'grant_sha256':grant_sha256,'conversations':conversations,'text_records':count,
               'private_database_readback_verified':True,'attachments_semantically_reviewed':False,
               'full_live_account_access':False,'model_weight_training':False}
    raw = json.dumps(receipt, separators=(',',':'))
    temp = directory / 'chatgpt-export-status.tmp'
    temp.write_text(raw)
    temp.chmod(0o600)
    temp.replace(directory / 'chatgpt-export-status.json')
    if status(directory) != receipt:
        raise ValueError('export_receipt_readback')
    return receipt


def import_configured(directory):
    url = os.environ.get('MSW_CHATGPT_EXPORT_URL','')
    if not url:
        return status(directory)
    trusted_url(url)
    grant = hashlib.sha256(url.encode()).hexdigest()
    current = status(directory)
    sha = current.get('archive_sha256','')
    if current.get('state') == 'imported_verified' and current.get('grant_sha256') == grant and (directory / 'chatgpt-imports' / (sha + '.sqlite3')).is_file():
        return current
    root = directory / 'chatgpt-imports'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = root / 'download.tmp'
    if temporary.is_symlink():
        raise ValueError('export_download_link')
    total = 0
    try:
        request = Request(url, headers={'User-Agent':'MSW-Owner-Authorized-Export/1.0'})
        with build_opener(ExportRedirect()).open(request, timeout=60) as response, temporary.open('wb') as target:
            temporary.chmod(0o600)
            for chunk in iter(lambda: response.read(1024 * 1024), b''):
                total += len(chunk)
                if total > MAX_ARCHIVE:
                    raise ValueError('export_download_limit')
                target.write(chunk)
        archived = root / (fingerprint(temporary) + '.zip')
        temporary.replace(archived)
        return import_zip(archived,directory,grant_sha256=grant)
    finally:
        temporary.unlink(missing_ok=True)
