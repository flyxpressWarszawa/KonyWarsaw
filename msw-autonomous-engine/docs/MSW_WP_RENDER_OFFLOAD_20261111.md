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

## Wybrany pilot źródłowy: `seo.analyze` — czysty Python, bez powrotu obliczeń do PHP

- **Konkretny kod:** `msw_engine/tasks.py::seo_analyze` liczy słowa, częstości terminów, H1–H3, obrazy ALT, odnośniki lokalne/zewnętrzne na danych już dostarczonych w `payload`; ograniczenia: `content <= 100000`, `html <= 140000`. W tej funkcji nie ma HTTP, wpdb, wywołań PHP ani dostawcy modelu.
- **Zdolność wykonawcza:** `BASE_HANDLERS['seo.analyze']` oraz `handlers()` udostępniają tę rodzinę Python workerowi; także w ograniczonym `MSW_RUNTIME_MODE=render-free`. **Obecność w repo nie potwierdza aktywacji na docelowej instancji.**
- **WordPress pozostaje właścicielem trwałego efektu:** `RemoteBridge::published` tworzy autoryzowany snapshot publicznego wpisu i kolejkuje `seo.analyze`; `RemoteBridge::applyResults` przyjmuje zgodny hash wyniku, sprawdza `post_status=publish` i aktualny `revision_digest`, zapisuje `_mswue_seo_analysis_v1` (małe postmeta), a następnie aktualizuje `applied`. Nie przenosić obliczeń do `applyResults`; jednocześnie realny hook `mswue_remote_result` należy do oddzielnego toru callback, więc **nie zmieniać go tutaj**.
- **Dotychczasowy wynik:** `seo.analyze` ma 0 zadań oczekujących w zaobserwowanym momencie. Zatem nawet idealny pilot tej rodziny **nie obniży aktualnego CPU**, jeśli nie istnieje stary równoległy lokalny analizator do wyłączenia. Na etapie przygotowania nie wolno deklarować redukcji.
- **Nowe dowody offline:** `tests/test_seo_render_pure_compute.py`: syntetyczne duże HTML/tekst, deterministyczność, lokalny dispatcher, brak call `msw_engine.federation.gateway`, brak zapisu do katalogu, limit rozmiaru i brak fallback do sieci/PHP. Zintegrowane z oddzielnym CI; żadne rzeczywiste wpisy klientów nie są odczytywane.

### Warunek E2E — zanim wyłączymy choć jeden stary hook

1. Udokumentować właściwy Render workspace + `service_id` i działający deploy SHA, aktywny HMAC node grant `seo.analyze`, trwały dysk oraz podpisany globalny STOP/HOLD/epoch. Bez tych dowodów produkcja = HOLD.
2. Backup WordPress (pliki i MariaDB) oraz Worker (konfiguracja/dysk, journal) z niezależnym odtworzeniem i zweryfikowanym rollbackiem. Wydajny monitoring CPU musi działać **przed** testem.
3. Zidentyfikować **konkretny istniejący, publiczny, zatwierdzony** rekord SEO (lub syntetyczny rekord wyłącznie na stagingu) i stabilny identyfikator żądania; nie publikować nowego posta tylko do testu bez osobnego zezwolenia. Sprawdzić `post_id`, `revision_digest`, `payload_sha256`, typ i kompletność danych.
4. Na kontrolowanym *stagingu* lub po bramkach produkcyjnych: jeden `claim` `seo.analyze` na uprawnionym Render, heartbeat, wykonanie w Python bez `gateway`, trwały local receipt, `complete`, status/readback z identycznym `result_sha256` i tożsamością request/lease.
5. Niezależny readback docelowego `_mswue_seo_analysis_v1` w WordPress: zgodność `job_id` i `result_sha256`, `applied=1`; dla zmienionego wpisu `applied=2` i zero wycieku przestarzałej analizy. Nie opierać się wyłącznie na HTTP 200.
6. Wymuszony test awarii tylko na izolowanej kopii: powtórzony ACK, równoczesny claim, utrata odpowiedzi po zapisie efektu, rollback — bez ponownego obliczenia lub podwójnego zapisu; scenariusze callback/recovery realizuje ich osobny właściciel. STOP w dowolnym momencie ⇒ brak kolejnego efektu.

### Pomiar CPU przed/po — kryteria akceptacji

Źródło **musi pochodzić z tego samego hostingu WordPress/cPanel i tej samej usługi Render**. WPVibe `worker_ms`, `network_ms`, `wp_php_ms` i HTTP status *nie są* pomiarem CPU. Zewnętrzne aktywności SEO/FotoAtlas/Hunter, zmiany traffic/cache i kilka workerów naraz zniekształcają wynik.

| Miernik | Przed i po | Bezpieczny warunek |
|---|---|---|
| WP CPU (% ograniczenia konta/serwera) | Co 60 s, co najmniej 15 min w porównywalnych oknach | Proponowane >=20% mniej CPU **na 1000 podobnych requestów** lub na 1 analogiczne zadanie, dopiero po weryfikacji źródła i obciążenia |
| PHP worker count i RAM/OPcache | Ten sam sampler/okno | Nie wzrastają, brak OOM |
| 5xx / TTFB p95 i request rate | Te same hosty, cache state i ruch | Brak wzrostu 503/504; p95 TTFB nie gorszy >10% (proponowany próg) |
| Render CPU/RAM, czas na zadanie, koszt | Ten sam worker i typ `seo.analyze` | Brak przeciążenia i nowej płatnej usługi/wywołania; koszt dodatkowy zero |
| Queue age, ACK i effect readback | Z tego samego `job_id`, `request_id` i hash | 1 efekt lub jawny HOLD; zero duplikatów, utraconych efektów lub fałszywych applied |

**Metryki liczbowe to kryteria proponowane, nie aktualne pomiary ani zatwierdzone SLA.** Jeśli brak realnego licznika CPU na właściwym serwerze, wyniki porównania = `NOT_MEASURED` i nie wolno deklarować ulgi dla WordPressa. Nie obniżać bezpieczeństwa ani nie zmieniać planu Render w celu osiągnięcia progu.

## HERMES-OFFLOAD-03 — bounded acceptance evidence, 11 Nov 2026

**No production move was authorized or made.** This is a single-pilot `seo.analyze` review. No new job, POST, paid inference, cron, instance, external callback, or replay was issued.

### Passive WordPress E2E readback of a PREEXISTING completed job

An admin-authorized WP-CLI **SELECT-only** query against `ovse_mswue_remote_jobs` found one earlier `seo.analyze` job (9 Nov 2026 10:24:20 UTC) under the recorded node `msw-render-auxseo-20261107-2032`. We checked the exact job's fields without returning any text, title, URL or client data:

- `kind=seo.analyze`; `state=completed`; `applied=1`; `result.ok=true`.
- In the *existing WordPress row*, SQL SHA2 over saved raw `payload` equals `payload_sha256` and over raw `result` equals `result_sha256`. Both independently read back as true.
- The existing linked WordPress post is `publish`, not password-protected, and its **current** title/content/status revision digest still matches the queued `revision_digest`.
- Exactly **one** existing postmeta entry `_mswue_seo_analysis_v1` contains the matching preexisting job UUID and `result_sha256` (read-only comparison, no output of private metadata). This is stronger than merely `state=completed`.
- Rollup of **1,587** previously completed `seo.analyze` rows: `applied=1` for 1,458 and `applied=2` (source stale) for 129; `applied=0` for 0. This is an historical WordPress store snapshot, not proof of exactly-once execution across all jobs.

**Interpretation:** one historical `WordPress queue -> recorded external worker result -> native WordPress postmeta` path has durable hashes and effect readback. It **does not** prove active Render service ID/commit SHA, current node grant, global STOP/HOLD signer+epoch, real Hermes authority, duplicate-proof callback under a lost ACK, or a NEW production E2E test. The last point belongs to the separate callback/recovery owner.

### Active worker/grant boundary

- WordPress `/msw-unified-engine/v1/remote/status` reported signed worker contacts: central `msw-render-central-1` and auxiliary `msw-render-auxseo-20261107-2032`, with observed `seo.analyze` in the latter's capabilities at **2026-11-11T20:43:03Z**. This establishes reported scope and recent contact at WordPress, not current source equality or independent Render process identity.
- The only visible Render workspace is named `Robot`. The Render connector requires explicit owner confirmation before selecting any workspace. Do **not** infer that `Robot` is the production target or start/deploy a worker there. **Target Render service/commit/disk/current grant: NOT_VERIFIED.**
- `msw-cpanel/doctor-status` returned `site_recovery_verified=false`. No successful independent WordPress+MariaDB+Render persistent-disk backup/restore test was available. **Backup/rollback gate: HOLD.**

### Native Atlas is NOT equivalent — source-backed negative check

The installed WordPress `msw-ai-seo-atlas/msw-ai-seo-atlas.php::check_post()` has `short_content` at **<500** words, `editorial_800` at <800, `title_long` at **>90** characters (with a different tokenizer). In contrast, `msw_engine/tasks.py::seo_analyze()` emits `short_body` at **<300** words, `long_title` at **>75** characters, with different parsing and no native editorial score. One synthetic case with 400 words and 80 title characters produces Python `long_title` and NO `short_body`, while the WordPress Atlas source would flag short content but not overlong title. This is intentionally NOT a 1:1 substitution. PR #30 added explicit `atlas_cutover_allowed=false` and `atlas_equivalent_verified=false` fields, plus tests. **Never disable or reconfigure native Atlas** on the strength of this pilot. Only true compute preparation, not a business/SEO-rule migration, is in scope.

### CPU baseline and release stop

- Read-only cPanel `ResourceUsage/get_usages` on the WordPress host at **2026-11-11 20:59:13 UTC** reported CPU **90/100**, 30/65 processes, 6/50 entry processes, RAM 649,912,320/2,147,483,648 bytes.
- The account pressure guard at **20:59:03 UTC** reported `pressure=true`, CPU **78%**, RAM **25.24%**; in its preceding seven samples CPU varied between 35 and 100%. These are BEFORE measurements only. **AFTER: NOT_MEASURED**.
- CPU pressure exceeds the existing MSW 70% alarm. Production pilot MUST NOT start under this pressure, even if a source-only test passes.
- E2E new job may run only with verified Render identity/grant+STOP epoch, independently *restored* backup, isolated rollback, and a stable low-pressure window. Use one already-authorized zero-paid SEO job or isolated fixture; after dispatch require same id, payload hash, lease, receipt hash and native postmeta readback. No reissue on uncertain ACK. Capture WP normalized CPU, RPM, PHP load, 503/504 and Render CPU/disk before and after.
- On preflight failure / clock drift / missing backup / stale source / pressure >=70%: no `claim`, no `complete`, no `applyResults`, no Atlas switch; evidence-only HOLD.

### Test/CI status

- Earlier source-only `offload_triage.py` snapshot: **7/7 PASS** in GitHub Actions run `43094438902` (SHA `61ba44d...`).
- Later expanded suite targets **12 offline tests** including a real pure-Python `seo_analyze` on large synthetic input, no PHP gateway, and a negative Atlas compatibility assertion; GitHub Actions latest run must be checked against the **exact branch HEAD** before claiming PASS. All old and new tests use no paid API.
- Current code changes are confined to PR #30; no production WP files, Render service settings or node grants changed. The separate Work callback track remains untouched.
