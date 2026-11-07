"""Import public security research metadata into a separate Doctor RAG corpus.

Catalog hits are unverified candidates, not recommendations or instructions.
No code is downloaded, installed, activated, or executed by this module.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .tasks import knowledge_ingest, knowledge_search


CATALOG = Path(__file__).with_name('security_catalog_20261107.json')
SCHEMA = 'msw.doctor.security-research-catalog.v1'
ALLOWED_HOSTS = frozenset(('wordpress.com', 'www.crn.com', 'files.cyberriskalliance.com',
                           'www.beauhurst.com', 'compworth.com', 'fortune.com', 'www.msspalert.com'))


def clean(value, maximum=240):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError('catalog_text_invalid')
    if any(ord(char) < 32 for char in value):
        raise ValueError('catalog_text_invalid')
    return value.strip()


def safe_source(value):
    if not isinstance(value, str) or len(value) > 1000:
        raise ValueError('catalog_url_invalid')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS or parsed.username
            or parsed.password or parsed.port not in (None, 443) or parsed.fragment):
        raise ValueError('catalog_url_invalid')
    return value


def load_records(path=CATALOG):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 1_000_000:
        raise ValueError('catalog_file_invalid')
    data = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(data, dict) or data.get('schema') != SCHEMA
            or not isinstance(data.get('plugins'), list) or not isinstance(data.get('companies'), list)
            or len(data['plugins']) + len(data['companies']) > 2000):
        raise ValueError('catalog_schema_invalid')
    records, ids = [], set()
    for item in data['plugins']:
        if not isinstance(item, dict): raise ValueError('catalog_plugin_invalid')
        slug = item.get('slug')
        if not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9-]{1,180}', slug):
            raise ValueError('catalog_plugin_invalid')
        name, author = clean(item.get('title')), clean(item.get('author') or 'Unknown')
        source = safe_source(item.get('url'))
        if source.rstrip('/') != 'https://wordpress.com/plugins/' + slug:
            raise ValueError('catalog_plugin_url_mismatch')
        categories = item.get('search_categories', [])
        if not isinstance(categories, list) or len(categories) > 20:
            raise ValueError('catalog_plugin_invalid')
        categories = [clean(x, 80) for x in categories]
        ident = 'doctor:security:plugin:' + slug
        text = ('UNVERIFIED PUBLIC CATALOG CANDIDATE; no installation or safety approval. '
                + ('Deprecated notice found. ' if item.get('deprecated_notice') is True else '')
                + 'Plugin: ' + name + '; author: ' + author + '; search categories: ' + ', '.join(categories)
                + '. Compatibility, vulnerability status and license require fresh independent checks.')
        records.append({'id': ident, 'approved': True, 'title': name, 'text': text, 'url': source,
                        'provenance': 'wporg_public_metadata_unverified_candidate_not_install_instruction'})
    for item in data['companies']:
        if not isinstance(item, dict): raise ValueError('catalog_company_invalid')
        name = clean(item.get('name'))
        sources = item.get('sources')
        if not isinstance(sources, list) or not 1 <= len(sources) <= 12:
            raise ValueError('catalog_company_invalid')
        refs = []
        for source in sources:
            if not isinstance(source, dict) or source.get('year') not in (2024, 2025, 2026):
                raise ValueError('catalog_company_invalid')
            refs.append((safe_source(source.get('url')), source['year']))
        ident = 'doctor:security:company:' + hashlib.sha256(name.casefold().encode()).hexdigest()[:24]
        text = ('UNVERIFIED RESEARCH CANDIDATE; no endorsement, current-status verification, '
                'relationship or product assessment. Company name: ' + name + '. Source years: '
                + ', '.join(str(year) for _, year in refs) + '. Older listings can be stale.')
        records.append({'id': ident, 'approved': True, 'title': name, 'text': text, 'url': refs[0][0],
                        'provenance': 'public_security_ranking_metadata_unverified_candidate'})
    for record in records:
        if record['id'] in ids: raise ValueError('catalog_duplicate_id')
        ids.add(record['id'])
    return records


def import_catalog(data_dir, *, path=CATALOG):
    target = Path(data_dir) / 'doctor-security-research'
    if target.is_symlink(): raise ValueError('unsafe_catalog_directory')
    records = load_records(path)
    totals = {'stored': 0, 'unchanged': 0, 'readback_verified_records': 0}
    for offset in range(0, len(records), 50):
        result = knowledge_ingest({'records': records[offset:offset + 50]}, target)
        for key in totals: totals[key] += result[key]
    return {'state': 'imported', 'records': len(records), **totals,
            'classification': 'unverified_public_research', 'plugins_installed': 0,
            'training_performed': False}


def search_catalog(data_dir, query, *, limit=5):
    target = Path(data_dir) / 'doctor-security-research'
    if target.is_symlink(): raise ValueError('unsafe_catalog_directory')
    result = knowledge_search({'query': query, 'limit': limit}, target)
    result['classification'] = 'unverified_public_research'
    for item in result.get('items', []):
        item['classification'] = 'unverified_public_research'
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Import or search unverified Doctor security research metadata.')
    parser.add_argument('action', choices=('import', 'search'))
    parser.add_argument('--data-dir', required=True, help='Existing private Doctor data directory')
    parser.add_argument('--query', default='')
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    if data_dir.is_symlink() or not data_dir.is_dir():
        parser.error('data-dir must be an existing non-symlink directory')
    if args.action == 'search' and not args.query.strip():
        parser.error('search requires --query')
    result = import_catalog(data_dir) if args.action == 'import' else search_catalog(data_dir, args.query)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
