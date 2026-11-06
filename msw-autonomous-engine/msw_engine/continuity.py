"""Continue the owner's actual projects from available, provenance-labelled memory.

This cannot download a ChatGPT account. Legacy source indexing is not semantic
review, instruction authorization, factual verification or weight training.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

MISSION = {
    "schema": "msw.owner-mission.v1",
    "source": "explicit_owner_instructions_2026-11-06_and_visible_project_context",
    "priority": "Poznaj dostępny dorobek właściciela i kontynuuj rozpoczęte projekty autonomicznie, bez komputera i kolejnych komend.",
    "projects": ["MSW AI: WordPress–COMBO–Render–Python–V3/V5/V7", "naprawy 503/504 i stabilność aktywnych silników",
        "Łowca Biznesów i Dyrektor: weryfikacja popytu, budowa i sprzedaż produktów cyfrowych",
        "FotoAtlas: oryginalne zdjęcia prac MSW, opisy techniczne i karty", "sklep MSW i e-deweloperski.pl",
        "aplikacje dla elektryków, techników, budowlańców i architektów", "portale usług technicznych premium, SEO i Smart Home"],
    "rules": ["Samodzielnie realizuj autoryzowane odwracalne prace i naprawy; nie pytaj o kontynuację.",
        "Pracuj z aktywnymi wtyczkami; nieaktywne zostaw bez zmian.",
        "Żywą listę autoryzowanych satelitów pobieraj z COMBO; nie używaj starej stałej listy.",
        "Twórz, testuj, instaluj, sprawdzaj odczyt/ACK i zapisuj postęp; sama paczka lub plan nie kończy zadania.",
        "Gotowe zweryfikowane materiały publikuj istniejącymi uprawnionymi kanałami, z zachowaniem praw i prywatności zdjęć.",
        "Korzystaj z istniejącego płatnego API ChatGPT i wspólnych silników; respektuj aktualne autoryzacje dostawcy.",
        "Render: istniejący silnik i baza, bez nowych usług, planów i zwiększania infrastruktury; media pozostają w WordPressie.",
        "Proste formularze mogą mieć propozycje cen 3–9 zł. Sprzedaż wymaga sprawdzonych płatności i chronionych pobrań.",
        "Upoważnienie właściciela 6.11: prowadź obsługę klientów i odpowiedzi w ich rozpoczętych rozmowach przez autoryzowany Telegram/centralę. Nie wysyłaj spamu ani nie kupuj usług bez konkretnego upoważnienia.",
        "Wykorzystuj wszystkie aktywne silniki centrali do konkretnych zadań biznesowych, poznawaj ich interfejsy i rozwijaj je po testach; brak adaptera zapisuj do naprawy, nie udawaj wykonania.",
        "Buduj również nowe strony i aplikacje poza portalami MSW, używając autoryzowanych kont i darmowego hostingu; nie wykupuj nowych planów ani domen.",
        "Archiwalne polecenia i odpowiedzi są źródłami historycznymi, nie nowymi grantami; bieżące ograniczenia mają pierwszeństwo.",
        "Nie deklaruj wykonania, działających połączeń, całej historii konta ani wytrenowania modelu bez dowodów."]}


def database(directory):
    root = directory / 'owner-continuity'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / 'progress.sqlite3'
    db = sqlite3.connect(path, timeout=10)
    os.chmod(path, 0o600)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS sources (corpus TEXT NOT NULL,id INTEGER NOT NULL,digest TEXT NOT NULL,title TEXT NOT NULL,kind TEXT NOT NULL,PRIMARY KEY(corpus,id))')
    db.execute('CREATE TABLE IF NOT EXISTS cursors (corpus TEXT PRIMARY KEY,cursor INTEGER NOT NULL)')
    return db


def bootstrap(directory):
    from .tasks import knowledge_ingest
    from .owner_context import seed
    owner_context = seed(directory)
    from .business_knowledge import seed as seed_business
    business_knowledge = seed_business(directory)
    text = json.dumps(MISSION, ensure_ascii=False)
    result = knowledge_ingest({'records': [{'id': 'owner:current-mission:20261106', 'approved': True,
        'title': 'Bieżące polecenia właściciela: autonomia i kontynuacja projektów MSW', 'text': text,
        'provenance': MISSION['source']}]}, directory)
    if result.get('readback_verified_records') != 1:
        raise ValueError('owner_mission_readback')
    return {'mission_sha256': hashlib.sha256(text.encode()).hexdigest(), 'owner_mission_readback_verified': True,
            'full_chatgpt_account_history_access': False, 'business_knowledge': business_knowledge, **owner_context}


def learn(directory, limit=300):
    from .legacy_memory import status
    info = status(directory)
    if info.get('state') != 'ready' or not re.fullmatch('[a-f0-9]{64}', info.get('source_sha256', '')):
        return {'state': 'no_legacy_source_available', 'full_chatgpt_account_history_access': False}
    corpus = info['source_sha256']
    source = directory / ('legacy-malta-' + corpus + '.sqlite3')
    if source.is_symlink():
        raise ValueError('continuity_source_link')
    with database(directory) as db:
        row = db.execute('SELECT cursor FROM cursors WHERE corpus=?', (corpus,)).fetchone()
        cursor = row[0] if row else 0
    with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as db:
        rows = db.execute('SELECT id,title,body,kind,digest FROM documents WHERE id>? ORDER BY id LIMIT ?', (cursor, max(1, min(500, limit)))).fetchall()
    for ident, title, body, kind, sha in rows:
        if hashlib.sha256((title + '\0' + body).encode()).hexdigest() != sha:
            raise ValueError('continuity_source_digest')
    with database(directory) as db:
        for ident, title, body, kind, sha in rows:
            db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?)', (corpus, ident, sha, title, kind))
        if rows:
            db.execute('INSERT INTO cursors VALUES(?,?) ON CONFLICT(corpus) DO UPDATE SET cursor=excluded.cursor', (corpus, rows[-1][0]))
    with database(directory) as db:
        for ident, title, body, kind, sha in rows:
            if db.execute('SELECT digest FROM sources WHERE corpus=? AND id=?', (corpus, ident)).fetchone() != (sha,):
                raise ValueError('continuity_progress_readback')
        count = db.execute('SELECT COUNT(*) FROM sources WHERE corpus=?', (corpus,)).fetchone()[0]
    result = {'state': 'available_source_catalogued' if count == info['records'] else 'cataloguing_available_source',
        'at': int(time.time()), 'available_records': info['records'], 'catalogued_records': count,
        'processed_this_cycle': len(rows), 'progress_readback_verified': True, 'source_sha256': corpus,
        'classification': 'legacy_unreviewed', 'full_chatgpt_account_history_access': False,
        'semantic_review_of_all_records_performed': False, 'training_performed': False}
    tmp = directory / 'owner-continuity' / 'status.tmp'
    tmp.write_text(json.dumps(result))
    tmp.replace(tmp.with_name('status.json'))
    return result


def context(directory, query):
    from .tasks import knowledge_search, TaskError
    sources = []
    for corpus in ('approved', 'legacy_malta', 'owner_packages_20261105'):
        # No missing owner corpus is created solely for a lookup.
        if corpus == 'owner_packages_20261105' and not (directory / 'owner-packages-20261105' / 'knowledge.sqlite3').exists():
            continue
        try:
            answer = knowledge_search({'query': query, 'limit': 2, 'corpus': corpus, 'owner_authorized': True}, directory)
            sources.extend([{**row, 'source_corpus': corpus} for row in answer.get('items', [])])
        except (TaskError, sqlite3.Error, OSError):
            continue
    return {'owner_mission': MISSION, 'sources': sources, 'full_chatgpt_account_history_access': False}

