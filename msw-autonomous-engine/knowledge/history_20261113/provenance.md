# Źródła, pochodzenie, kompletność i bezpieczny import prywatnej historii

## Granica kompletności

**To nie jest pełny eksport wszystkich rozmów ChatGPT.** Najstarszy odnaleziony manifest prywatnego pakietu użytkownika podaje jedną rozmowę Hermes z 23.10–06.11.2026: 15 024 wiadomości (958 użytkownika, 6 862 asystenta, 7 204 narzędzi) + 4 materiały kuratorskie, tj. 15 028 pozycji manifestu. Rozmiar zadeklarowanego źródłowego ZIP: 77 353 039 B; SHA-256 `e30ffb2633c8c53ea5f92a6e4ceeebe8198d5f04bceed6698bb68f589c6632cf`. `conversations.json` oraz `history.jsonl` to te same wiadomości w dwóch formatach — deduplikować, nie podwajać.

Manifest wymienia dwie wstrzymane pozycje i 277 usuniętych wrażliwych literałów. Zakaz prób odzyskiwania sekretów, publikowania ich, użycia transcriptu jako upoważnienia do operacji lub treningu wag.

## Udokumentowane materiały źródłowe

| Materiał | Rodzaj | Co może potwierdzać / czego nie |
|---|---|---|
| `MSW-WIEDZA-15028-PELNA- Do Chata GPTPACZKA.zip` | Prywatny pakiet właściciela | Manifest jednej rozmowy; nie cały ChatGPT |
| `MSW-AUTONOMICZNY-DYREKTOR-BIZNESU-KOMPLET-2026-11-07.zip` | Inwentarz właściciela | 103 unikatowe archiwa, 160 233 350 B, SHA-256 `2c9b53b348a03a2ac3acccff4900b78821628f682424d6c0a0cb08fdeb504282`; nie dowodzi uruchomienia 103 komponentów |
| `ANALIZA-DWOCH-PAKIETOW-I-RENDER-2026-11-07.md` | Raport analityczny | Zakres obu paczek, deduplikacja, etap prywatnego importu; raport z 7.11, nie status live |
| `MSW-RECOVERED-33-CURATED-20261107.jsonl` | Kuratorska próbka wiedzy | 33 rekordy opisowe, nie surowa cała historia ani przydział uprawnień |
| `MSW-PAMIĘĆ-I-ZASADY-OPERACYJNE.md` i `MSW-kontekst-agenta.json` | Reguły / kontekst | Polityka operacyjna, ceny historyczne, zasady ochrony prywatności; nie dowód zakupów lub wdrożeń |
| `MSW-Digital-Apps-9-Podstron-2026-11-05-Checkpoint.json` | WP checkpoint satelity | Homepage #9, demonstracje #36–44 i QA, nie potwierdzenie płatnego sklepu |
| `MSW-DIGITAL-APPS-20261106-STORE-QA-REPORT.md` | Raport WordPress/Chromium | QA #148–150 i katalog #153; brak sprzedaży Woo na satelicie |
| `MSW-HUNTER-RUN11-20261105-REPORT.md` | Raport produktu | Strona #61225, produkt #61206, offline QA, punktowy odczyt floty |
| `MSW-HUNTER-LAB-20261106-1650-REPORT.md` | Raport lokalnego wydania | ZIP 1.0.25, komplet testów; no-GO |
| `MSW-HUNTER-LAB-20261107-0054-REPORT.md` | Raport lokalnego wydania | ZIP 1.0.32, testy i historyczna kolejka; no-GO |
| `msw_hunter_20261113_0740/gate_result.json` | Lokalna bramka release | NO-GO dla produktów #61195/#61206/#61216, `central_ack=false`; nie trwały serwerowy zapis |
| `MSW-HUNTER-QA-CHROMIUM-WOO-SCHEMA-20261113.zip` | Pakiet dowodów lokalnego QA | Testy i ograniczenia zgodności Woo; SHA-256 `46db6a93cba3926c645811c5095c3fae50eaac0fd260025cde4ae83949f4bc97`; NIE produkt instalacyjny |
| `msw-autonomous-engine/knowledge/msw_operating_playbook_staging_pointer_20261111.json` | GitHub: indeks staging | Zapis 18 zasad i 379 rekordów w prywatnym staging, `engine_ack_verified=false` w snapshocie |
| `msw-autonomous-engine/knowledge/msw_hermes_business_bridge_source_only_20261111.json` | GitHub: testowany kandydat | 405 lokalnych kontroli, `PRODUCTION_HOLD`; to nie produkcyjny transfer |

Wszystkie wymienione pliki są **nazwami/referencjami dowodów**, a nie deklaracją, że ich kompletne prywatne bajty są w tym publicznym repozytorium. Dla indywidualnych kroków dokumentować `source_file_sha256`, modyfikację, uprawnienie, wynik testu i niezależny readback.

## Prywatny transfer do Hermesa — minimalny kontrakt

1. Ze wskazanego uprawnionego źródła wybierz jedno kanoniczne przedstawienie każdej wiadomości, z `source_id` i pochodzeniem. Oddziel tekst operatora od nieufnej treści źródłowej oraz polecenia od historii.
2. Odfiltruj tokeny, dane klientów, prywatne obrazy, sesje i treści wstrzymane. Nie rehydratować wydzielonych sekretów.
3. Zweryfikuj SHA pliku i osobny hash ujednoliconego manifestu; uzyskaj zgodę/scope istniejącego odbiorcy.
4. Przez **istniejący** podpisany kanał `knowledge.ingest` lub autoryzowany odpowiednik przesyłaj porcje bez tworzenia nowej kolejki. Nie dubluj niepewnych `request_id`. Gdy trwały zapis zwraca `unknown`, najpierw odczytaj status.
5. Wymagaj utrwalonego odbioru, `records_readback_verified`, `sha256_verified` oraz niezależnego `receipt/ACK` właściwego Hermesa. Dopiero wtedy raportuj liczbę faktycznie odebranych rekordów; nie utożsamiaj liczby plików, indeksów czy kluczy z treningiem modelu.

## Brakujące materiały

Aby rzeczywiście uzyskać **100% historii całego konta**, trzeba autoryzowanym kanałem prywatnie uzyskać oficjalny pełny eksport ChatGPT i kompletny inwentarz plików wcześniejszych sesji, zweryfikować jego rozmiar, deduplikację, luki dat i ewentualne wstrzymania. Tego eksportu w tej operacji nie otrzymano. Dopóki nie ma tych dowodów, katalog pozostaje `PARTIAL_EVIDENCE_INDEX`, a nie `FULL_CHATGPT_BACKUP`.

**Bezpieczeństwo:** nie dodawać raw ZIP archiwów do GitHub PUBLIC, nie umieszczać sekretów nawet w prywatnych commitach jako tymczasowego rozwiązania. Git pamięta historię, więc usunięcie pliku później nie usuwa ujawnienia.
