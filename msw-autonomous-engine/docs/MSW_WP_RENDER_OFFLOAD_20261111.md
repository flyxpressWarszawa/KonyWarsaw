# MSW — WordPress → Render: odciążanie bez utraty efektów (11.11.2026)

**Stan: SOURCE-ONLY / PRODUCTION HOLD.** Cel właściciela: przenieść obliczenia poza WordPress, używając istniejących zasobów Render, bez nowego planu płatnego i bez utraty obsługi klientów. Ten dokument jest odbiorem wstępnej, nieinwazyjnej diagnozy i planem realizacji, nie dowodem wdrożenia.

## Rzeczywisty punkt ciężkości

Odczyt połączonego WordPressa przez administracyjny WPVibe (read-only), SQL `SELECT state,kind,COUNT(*) ... GROUP BY`:

| Typ kolejki | Pending | Running | Co wynika ze źródła |
|---|---:|---:|---|
| `module.execute` | 9 791 | 1 | `msw_engine/native_autonomy.py` wywołuje z Rendera `gateway("autonomy_execute")`, a WordPress wykonuje `MSW_Module_Autonomy::execute` lub `MSW_Fleet_Autonomy::forward`. **To wciąż obciąża PHP i DB WordPressa**. Samo przeniesienie planisty na Render nie przenosi efektu. |
| `runtime.catalog` | 175 | 0 | Ma gałęzie z odczytem WordPressa i pamięcią lokalną. Nie jest zawsze obliczeniem czysto lokalnym. |
| `supreme.observe` | 118 | 0 | Zależne od prywatnego pakietu, dysku i readbacku — verify source before moving. |
| `knowledge.search` | 2 | 0 | Szuka w lokalnej SQLite/FTS. Potrzebuje potwierdzonej tożsamości bieżącego corpus na dysku Render. |
| `engine.v3.ingest` | 1 | 0 | Potencjalny efekt trwały, wymaga odrębnej walidacji. |
| `seo.analyze` | 0 | 0 | Kod Python ma czysty parser HTML i analizę słów. Pozostaje poprawnym kandydatem do istniejącego workera Render, ale w tym snapshotcie **nie ma takich zadań**. |

Wcześniejszy GET `/msw-unified-engine/v1/remote/status` pokazał `heavy_compute_runs_in_wordpress=false`, ale to statyczne pole raportowe — **nie** dowód, że wszystkie callbacki wykonują się na Renderze.

Odczyt `wp cron event list --format=json` pokazał 271 zdarzeń, z czego 232 pasują do filtrów MSW / FotoAtlas / Render / Hunter / Woo / Action Scheduler. Liczba nie mierzy kosztu CPU, lecz wskazuje dużą liczbę aktywnych harmonogramów. Nie usuwać cronów bez migracji właściciela hooka i potwierdzonego ACK.

## Co można odciążyć, a co musi pozostać w WordPressie

- **Dobre cele przeniesienia:** analiza SEO dokumentów, normalizacja tekstów, agregacje danych, klasyfikacje, raportowanie, przetwarzanie mediów po uprawnionej synchronizacji, niezależne treningi lokalnych modeli, niezależna analiza PDF/zdjęć.
- **Warunkowe:** wyszukiwanie/ingest wiedzy (trwałość, prawa źródeł i odczyt), badania rynku (nie zawsze offline — modele i źródła mają koszty), FotoAtlas (prywatność i potwierdzenie odbioru plików), `runtime.catalog` (wywołania z powrotem do PHP).
- **Natywne WordPress:** routing HTTP, autoryzacja, zamówienia, płatności, zapis postmeta/wp_options/produktów, hooki wtyczek, finalne potwierdzanie efektu. Render wykonuje część obliczeniową, ale nie zastępuje transakcji WP.
- **KRYTYCZNE:** w `RemoteBridge::applyResults` istnieje `do_action('mswue_remote_result', ...)` i potem zapis `applied`. Ten kontrakt należy zostawić właścicielowi toru callback/effect/idempotency, nie masowo wypychać do chmury.

## Kolejność robocza (bez nowe usługi i kosztów)

1. Dla obecnego workera Render potwierdzić **właściwy workspace i service_id, commit SHA, persistent disk, logi**, oraz porównać faktyczny scope `claim` z kodem. Lista workspaces wskazuje jedną przestrzeń `Robot`, ale **użytkownik nie potwierdził jeszcze** jej jako celu. Nie wybierać w ciemno.
2. Wykonać walidację STOP/HOLD/epoch oraz backup i próbne odtworzenie. Dopiero wtedy mały pilot z istniejącej kolejki dla *jednej* rodziny obliczeń bez efektu. Monitorować liczbę requestów WP, CPU/PHP/MySQL i kolejkę przed/po.
3. Dla `module.execute`: prowadzić listę rzeczywistych `hook → callback → storage/effect → idempotency`; każdy plugin migrować oddzielnym adapterem. Do momentu przejścia testów nie przenosić callbacków i nie wykonywać masowego claim / retry.
4. Render ma przenosić *obliczenia*, nie wydatki bez limitu. Paid `llm.generate` pozostaje pod niezależnym Budget Guard; nie uruchamiać modeli w teście. Nie aktywować dodatkowych workerów automatycznie.
5. Po każdej zmianie: porównać source SHA, liczbę i koszty HTTP do WordPressa, rzeczywisty zapis wyniku + readback i rollback. Dopiero wtedy wyłączyć **konkretny** stary hook, nie wszystkie.

## Narzędzie diagnostyczne w repo

`msw_engine/offload_triage.py` przyjmuje **tylko** grupowane wyniki `state,kind,jobs` (z WP read-only) i daje klasyfikację z wymuszonym `permission_to_deploy=false`. Nie przyjmuje payloadów klientów, nie wykonuje zadań ani połączeń. To najniższy poziom faktów potrzebnych do scalenia. Testy nie używają sieci, bazy produkcyjnej i płatnego API.

## Blokady

**OFFLOAD NOT VERIFIED:** brak potwierdzonego uprawnienia do docelowego workspace Render; brak zakończonej weryfikacji signer/epoch STOP; brak backup/restore PASS; brak źródłowego testu E2E aktywnego workera i wyników; brak migracji efektowych pluginów. Nie usuwać ani nie wyłączać 271 cronów w ciemno.

**Punkt odbioru:** 0 migracji produkcyjnych, 0 zmian WP/Render, 0 nowego crona i 0 wywołań modeli. Na tym etapie wykonano diagnostykę i przygotowano instrumentację odczytową.
