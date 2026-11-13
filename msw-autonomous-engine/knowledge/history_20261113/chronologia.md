# Chronologia MSW — odzyskane fakty, publikacje i checkpointy

**Okno odtworzenia:** najstarszy manifest rozmowy właściciela zaczyna się 2026-10-23; konkretne publikacje i wykonania poniżej zaczynają się od tych, dla których znaleziono materiał z ID, datą lub wynikami. Nie uznawać ich za pełną historię konta.

| Data / okres | Działanie lub decyzja | Stan dowodowy |
|---|---|---|
| 2026-10-23 – 11-06 | Archiwum rozmowy Hermes zadeklarowało 15 024 wiadomości + 4 dokumenty; dwie reprezentacje tych samych wiadomości | Manifest właściciela, nie kompletny eksport ChatGPT |
| 2026-10-31 | Zasada jednego rdzenia MSW (Central Engine, Supreme, V3/V5/V7, Owned LLM); bez klonów i konkurencyjnych kolejek; reguła append-only dla nowych własnych domen w plikach crawlera | Zapisana strategia operatora, nie potwierdzenie każdej realizacji |
| 2026-11-03 – 11-05 | Rozwój Business Hunter, Opportunity250, Cortex i COMBO; priorytet weryfikacji serwerowej kolejki oraz starych P0/P1 przed kolejnym researchem | Checkpointy; liczniki historyczne |
| 2026-11-05 | Centrala: publiczna strona nowych biznesów AI, WP #61102; portal `msw-digital-apps`, WP #61225, z oznaczeniem QA. Trzy niepubliczne szkice produktów Woo: #61195, #61206, #61216. Starszy osobny produkt Energia CSV: #25059 — nie dublować | Opis źródeł i odczyty WordPress w raportach MSW LAB |
| 2026-11-05 | Satelita `mswdigitalapps.pl`: homepage WP #9 w wersji QA oraz 9 stron demonstracyjnych #36–44 (akumulatory, UPS, fotowoltaika, smart home, elektronika, PoE, spawanie, sauna/SPA, dokumentacja). W checkpointcie: strona cofnięcia #7, bez niezależnego pełnego audytu bezpieczeństwa | Zapisane ID / wersje w checkpoint JSON |
| 2026-11-06 | Satelita: trzy osobne demonstracje QA Ultra, WP #149 Energia, #148 KartaCzęści, #150 Woo Import Lab; Fabryka #76 i katalog/sklep informacyjny WP #153, 19 demonstracji. Brak WooCommerce na satelicie, więc nie potwierdzono sprzedaży | Odczyt WordPress w raporcie 6.11; nie mylić publikacji DEMO z produktem płatnym |
| 2026-11-06 | Bridge WordPress 1.1.x rozbudowywany do wymiany z Hermesem; zachować delegowany cron `MSW_Module_Autonomy::capture`, podpisany transport, `request_id`, `receipt` i weryfikację efektu; były historyczne timeouty, HTTP404/502, zadanie Factory #586 bez zamknięcia | Punktowe diagnostyki z 6.11, bez automatycznej aktualności |
| 2026-11-06 | Trzy aplikacje v1.0.0 lokalnie zaliczyły 15 testów; brak potwierdzonego serwerowego ZIP z chronioną dostawą; rozwój kolejnych wersji lokalnego QA CSV | Archiwa i raporty; sprzedaż NO-GO |
| 2026-11-07 | Audyt dwóch paczek: historyczny zestaw 103 unikalnych archiwów oraz prywatna paczka Hermes 15 028; powstał kuratorski importer wiedzy, nie potwierdzono wtedy pełnego transferu na nowy Render | Raport analizy; nie eksportować raw do publicznego GitHub |
| 2026-11-07 – 11-12 | MSW LAB: sekwencyjne poprawki parserów CSV, walidatorów, odporności na formuły arkuszowe, jednostek i raportów; dziesiątki lokalnych cykli QA. Statusy `QA-NOT-LIVE` pozostają niezależne od produkcji | Lokalna historia testów, manifesty SHA i raporty; nie wszystkie zmiany wdrożono |
| 2026-11-11 | Istniejący indeks prywatnych pakietów w repo: `msw_operating_playbook_staging_pointer_20261111.json`, 18 zasad staging, 379 rekordów wiedzy w prywatnym staging; `runtime_readback_verified=false`, `engine_ack_verified=false` w tym dokumencie | Potwierdzona zawartość pliku GitHub, nie status live |
| 2026-11-12 – 11-13 | Rozwój mostów COMBO i Hermes; w logach aktualnego workera raportowano 22 334 rekordy katalogu z readbackiem jego zadania oraz 41 satelitów deklarowanych, ale tylko wycinek prac i błędy `business_http_503` | Punktowy log workera 13.11, nie 41 indywidualnych ACK |
| 2026-11-13 10:42 UTC | Lokalny audyt Woo Import Lab R7: 122/122 testy Node.js, 11/11 kontroli Chromium; SHA pakietu **dowodowego QA** (nie ZIP produktu) `46db6a93cba3926c645811c5095c3fae50eaac0fd260025cde4ae83949f4bc97`. Zidentyfikowano ograniczenia dla stock=parent, backorders, pustego stock i brakującego SKU przy ID | Lokalny manifest i raport 13.11; bez instalacji LIVE |
| 2026-11-13 | Opportunity250 v2.7.1: 363 hipotezy, kursor 55; ostatni ukończony raport wskazany jako #64230 z 5.11, Google SERP PL incomplete; brak nowej wiarygodnej okazji | Punktowy odczyt prywatnego rekordu; brak pomiaru nie jest zerem |
| 2026-11-13 | Inwentarz centrali raportował 333 wtyczki, w tym Cortex 3.9.1.1, COMBO 108.1.5, Native Factory 1.2.1.1, Bridge 1.1.9; obecność wtyczki ≠ dostęp/wykonanie/ACK dla każdej funkcji | Punktowy odczyt, wymaga odświeżenia |
| 2026-11-13 | Istniejący worker Render w obserwacji LIVE, lecz historyczne receipts Factory, pełny transfer wiedzy i pobrania produktów pozostają niezależnymi kwestiami do udowodnienia | Logi i kontrola release-gate z 13.11 |

## To, co nie jest znane i nie może być deklarowane

- **Pierwsza kiedykolwiek publikacja wszystkich stron MSW** — brak kompletnego indeksu historycznych wpisów WP z datami; powyższe to najwcześniejsze odzyskane fakty z określonymi ID, nie pewna data rozpoczęcia biznesu.
- Pełny eksport całego konta ChatGPT oraz wszystkich jego rozmów — nie potwierdzono; manifest 15 028 obejmuje jedną rozmowę Hermes.
- Dokładna aktualna lista wszystkich satelitów COMBO, ostatni ACK każdego z nich, sprzedane produkty, ruch, koszty i konwersje — wymaga żywych odczytów.
- Wyniki niepewnych POST/ticków i opóźnionych deploymentów — wymagają odczytu po `request_id` i wersji live; nie należy ponawiać w ciemno.

## Zasada prowadzenia chronologii

Każde uzupełnienie historii: `event_id`, data (UTC), `source_ref`, `source_sha256` jeśli dostępny, target/ID, status `verified|historical|not_verified|blocked`, rodzaj dowodu (publikacja, test, readback, ACK), zakres prywatności i następny krok. Historycznych liczb nie wolno mieszać z pomiarami tego samego dnia. Poprzednich rekordów nie nadpisywać bez uzasadnienia i aktualnego źródła.
