# P1 — Hermes: naprawa potwierdzeń knowledge.ingest

Data kontroli: 2026-11-13. Ten dokument jest **zapisanym zadaniem inżynierskim**, nie dowodem dostarczenia go uruchomionemu procesowi Hermes.

## Dowody
- WordPress: `/wp-json/msw-unified-engine/v1/remote/status` — centralny worker `msw-render-central-1` ostatnio ogłaszał tylko `llm.generate`; brak aktywnego `knowledge.ingest` w tej obserwacji.
- Wcześniejsze joby `knowledge.ingest` (przykładowo `d0014c17-2fe4-7163-9397-b59fdcf46e64`) są `failed`, `attempts=4`, `applied=0`; zapisanie do WP oznacza jedynie zarejestrowanie zlecenia, nie gotowy import.
- Logi Render: `receipt_recovery` ma `deferred_unverified` / `knowledge_effect_contract_required`.
- `/wp-json/msw-knowledge/v1/health` zwraca `receiver_delivery=not_configured`; lokalna baza CPT to nie Hermes.
- W gałęzi tego PR są `public-source-research-batch-008-20261113.json` i `public-source-research-batch-009-20261113.json`, każda po 100 dokumentów. Tekst stron jest nieufnym materiałem, nie API do bezpośredniego użycia.

## Minimalna poprawka do wykonania przez właściciela workera
1. Odczytać dokładne źródło działającego prywatnego entrypointa z dysku Render, a nie zakładać, że gałąź GitHub to produkcja.
2. Odzyskać wyłącznie istniejącą zdolność `knowledge.ingest`, zachowując podpisany transport, tokeny serwera, politykę kolejkowania, idempotency i własność katalogu SQLite.
3. Sprawdzić RED w izolacji na 1 krótkiej paczce; wymagać atomowego zapisu oraz niezależnego `readback_verified_records=accepted_records` i SHA-256 wyniku.
4. Następnie potwierdzić `completed` na WordPressie i trwały receipt; przy niepewnym efekcie **nie ponawiać**.
5. Dopiero po GO rozliczyć wcześniejsze joby i przekazywać wyniki badań w kolejnych 100-elementowych partiach, z licznikami realnie zapisanych pozycji.

Nie tworzyć nowego crona, płatnej infrastruktury ani nowych kont. Nie ujawniać sekretów do publicznego AI ani GitHub. `failed` może wymagać diagnostyki pierwotnej przyczyny przed powtórką. Do czasu GO paczki pozostają source-only w GitHub.

## Test wejścia do kolejki — 2026-11-13
- W świeżej odpowiedzi WordPress worker `msw-render-central-1` ponownie ogłosił `knowledge.ingest`, mimo wcześniejszego braku w rejestrze; możliwe przejściowe/naprzemienne ogłaszanie zdolności, nie potwierdzona stabilna naprawa.
- Jedna (1) paczka próbna została zapisana przez uwierzytelniony `POST /msw-unified-engine/v1/remote/jobs`: `job_id=91557273-dea0-3531-a128-ef769d52b11d`, `payload_sha256=ccdabd2cc751257eda00a034ec9c9f4b35b8decdb4f16965c4f92004f8146b35`.
- Kolejny odczyt z tabeli: `kind=knowledge.ingest`, `state=running`, `attempts=1`, `applied=0`, pusty `result_sha256`. Brak trwałego potwierdzenia wykonania. Render COMBO heartbeat pokazał `ack_pending`.
- Nie uruchomiono masowej retransmisji dokumentów, ponieważ `completed + SHA + readback` nie zostały potwierdzone.
