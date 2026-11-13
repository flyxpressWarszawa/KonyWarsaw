# START — centralne archiwum wiedzy MSW dla Hermesa

**Źródło prawdy:** stała gałąź `msw/hermes-knowledge-vault` w publicznym repozytorium `flyxpressWarszawa/KonyWarsaw`.  
**Zakres:** bezpieczna historia od najwcześniejszych zachowanych materiałów MSW (19.10.2026), metadane publikacji i działań, raporty badań publicznych, kod kandydatów bez sekretów oraz instrukcje do weryfikacji.  
**Ograniczenie:** nie zawiera prywatnych surowych rozmów ChatGPT 1:1, wszystkich kliknięć ani pełnego eksportu konta. Jedyna historyczna rozmowa z archiwum 15 024 wiadomości z 23.10–06.11 to osobne **prywatne** źródło. Nie wolno publikować go tutaj.

## Jeden link do pobierania

[Automatyczny indeks GitHub (publiczny)](https://raw.githubusercontent.com/flyxpressWarszawa/KonyWarsaw/refs/heads/msw/hermes-knowledge-vault/msw-autonomous-engine/knowledge/HERMES-GITHUB-AUTO-INDEX.json).

GitHub Actions na `main` aktualizuje go po wykryciu nowych plików w vault, bez obciążenia WordPressa. Workflow `msw-hermes-github-archive.yml`: co 15 min + ręczne wywołanie. Kolejny workflow `msw-hermes-vault-pull-tests.yml`: testy klienta read-only, bez praw do zapisu.

Najważniejsze dokumenty:
- `MSW-CHRONOLOGIA-OD-POCZATKU-20261019-20261113.md` — co tworzono, dlaczego, jakie silniki i co pozostaje nieweryfikowane.
- `HERMES-PRACE-I-PUBLIKACJE-LEDGER-20261113.jsonl` — kolejność 25 kluczowych zdarzeń ze statusami dowodów.
- `HERMES-GITHUB-SOURCE-MANIFEST.json` — manualny manifest partii 008–014 z SHA-256.
- `HERMES-GITHUB-AUTO-INDEX.json` — automatycznie regenerowany spis dokumentacji i źródeł.
- `public-source-research-batch-008-20261113.json` przez `014` — siedem partii po 100 źródeł, łącznie 700 unikalnych adresów.
- `HERMES-INGEST-BLOCKER-20261113.md` — brak trwałego receipt `knowledge.ingest`.
- `HERMES-CAPABILITY-TELEMETRY-DIAG-20261113.md` — poprawka telemetrii w przygotowaniu.
- `README-HERMES-GITHUB-SOURCES.md` — pełne zasady pobierania, deduplikacji, prywatności i odbioru.

## Jak pobrać automatycznie do lokalnego katalogu źródłowego

Użyj przetestowanego w GitHub Actions klienta `msw-autonomous-engine/tools/msw_hermes_github_pull.py`.

Przykładowo z działającej kopii repozytorium na Linux/Render:

```bash
python3 msw-autonomous-engine/tools/msw_hermes_github_pull.py \
    --dest /var/data/msw-hermes/public-source-staging
```

Windows (Python zainstalowany lokalnie):

```powershell
py msw-autonomous-engine/tools/msw_hermes_github_pull.py --dest "$env:USERPROFILE\Documents\MSW-Hermes-Zrodla"
```

Klient pobiera wskazane w auto indeksie pliki, porównuje SHA-256, zachowuje niezmienione dane bez ponownego transferu, odrzuca przekierowania/dziwne URL i symlinki, a wynik zapisuje do `HERMES-GITHUB-DOWNLOAD-RECEIPT.json`. **Nie uruchamia kodu ani automatycznie nie zapisuje danych do zatwierdzonego RAG.**

Uruchomienie tego skryptu cyklicznie wewnątrz aktywnego Hermesa/Render wymaga wdrożenia do istniejącego prywatnego workera. Obecne GitHub Actions utrzymuje automatycznie **archiwum**, a nie bezpośrednią pamięć Hermesa. Nie wolno obiecywać odbioru, gdy brak jego trwałego SHA/ACK.

## Bezpieczne dołączanie kolejnych dokumentów

Nowy dokument czy raport zapisuj w gałęzi `msw/hermes-knowledge-vault` jako `.md`, `.json` lub `.jsonl`, z wyraźnym pochodzeniem, datą, statusem i bez sekretów. Nowe publiczne badania tworzą `public-source-research-batch-NNN-YYYYMMDD.json` ze 100 rekordami oraz unikalnymi URL. Automat odrzuca błędne i powtarzające się paczki zamiast fałszywie podnosić licznik.

### Status dowodowy

- `GITHUB_ARCHIVED` — plik jest zapisany i zgodny z hashem.
- `DOWNLOAD_VERIFIED` — klient pobrał dokładnie te bajty.
- `HERMES_INGESTED` — istniejący Hermes zwrócił podpisany receipt po zapisie, zgodne SHA, licznik rekordów i niezależny odczyt.
- `PROVIDER_AVAILABLE` — osobny realny test dostawcy, opłat, licencji i konkretnego modelu.

Żadna z pierwszych dwóch kategorii nie oznacza ostatnich dwóch. Wobec błędu `knowledge_effect_contract_required` oraz dużej zaległej kolejki nie wykonuj ślepego importu czy retry.

Prywatne archiwa, w tym pakiet 103 ZIP i historia 15 024 wiadomości, pozostają w prywatnej bibliotece właściciela; do publicznego repozytorium trafiają tylko bezpieczna kronika i referencje źródłowe. Pełne archiwum konta ChatGPT wymaga odrębnego uprawnionego eksportu, nie można go uzyskać automatycznie z GitHub Actions.
