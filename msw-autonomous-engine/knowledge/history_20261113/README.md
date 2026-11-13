# MSW — archiwum przekazania wiedzy ChatGPT → GitHub → Hermes

**Stan katalogu:** 2026-11-13. **Charakter:** publiczny, zredagowany, tylko wiedza i dowody; nie jest zleceniem wykonania ani kolejką serwerową.

## Czytaj w tej kolejności

1. [Pierwsze odzyskane publikacje i źródła z września–października](./poczatki-2026-09-10.md) — datowane relacje o witrynach Sites i ośmiu stronach WP, z zastrzeżeniami dowodowymi.\n2. [Chronologia publikacji, projektów i decyzji](./chronologia.md) — historia możliwa do odtworzenia z dostępnych materiałów i checkpointów.
3. [Mapa systemu i granice potwierdzeń](./system-i-status.md) — COMBO, Hermes, WordPress, Render, cPanel, kolejki, AI, bezpieczeństwo i koszt.
4. [MSW Digital Apps i publikacje](./digital-apps.md) — trzy aplikacje, numery publicznych stron/produktów, aktualne testy QA i warunki sprzedaży.
5. [Przekazanie Hermesowi: manifest maszynowy](./hermes-handoff.json) — stabilny identyfikator, źródła prywatne, następny odczyt i wymagane ACK.
6. [Pochodzenie i kompletność materiałów](./provenance.md) — zakres dostępnych archiwów, braki i zasady importu prywatnej historii.

- [Początki projektu (wrzesień–październik 2026)](./poczatki-2026-09-10.md) — wcześniejsze ślady ZIP i rozwój portali z rozróżnieniem projektów od rzeczywistych publikacji.

## Co faktycznie jest tu zapisane

To **kuratorski, odtwarzalny indeks wiedzy projektu MSW**, nie dosłowny i kompletny zapis wszystkich czatów konta ChatGPT. Przeszukiwalne, dostępne tu źródła obejmują m.in. właścicielski manifest jednej rozmowy Hermes z 2026-10-23–2026-11-06, 15 024 wiadomości / 4 dokumenty kuratorskie (łącznie 15 028 pozycji), starsze checkpointy, znane publikacje WordPress, dokumentację repozytorium i testy 2026-11-13. Dwie reprezentacje tej samej historii (conversations.json/history.jsonl) nie są dwoma korpusami. **Nie przeprowadzono pełnego eksportu i niezależnego sprawdzenia całego konta.**

Repozytorium `flyxpressWarszawa/KonyWarsaw` jest PUBLICZNE. Nie ma tu surowych czatów, tajnych opcji WordPress, kodów dostępu HMAC, danych klientów, tokenów, eksportów sesji, modeli lub prywatnych baz. Pełna historia powinna trafiać wyłącznie do już istniejącego prywatnego kanału pamięci, po sprawdzeniu uprawnień, redakcji i odczycie SHA; GitHub stanowi **mapę źródeł i polityk**, nie magazyn prywatnych treści.

## Co Hermes ma zrobić z tym katalogiem

Hermes powinien pobrać te pliki w trybie READ-ONLY, sprawdzić repozytorium, ścieżki i SHA/revision, połączyć z **istniejącym** źródłem pamięci MSW i zapisać wynik przez dotychczasowy podpisany transport z indywidualnym `request_id`, `receipt` i niezależnym `readback`. Nie uruchamiać osobnej kolejki ani CRON. Sam link GitHub, issue, PR lub obecność plików **nie jest dowodem doręczenia, zaimportowania lub zrozumienia przez Hermesa**. Raportować `NOT_VERIFIED`, dopóki brak rzeczywistego potwierdzenia z działającego odbiorcy.

Nadrzędne zasady: nie dopuszczać zmian LIVE bez niezbędnych backupów plików i DB, testu odtworzenia, niezależnego skanu bezpieczeństwa, QA, canary i ACK; nie modyfikować cen, SKU, VAT, PayPal, uprawnień, budżetów i harmonogramów. Nie ponawiać niepewnych POST. Rejestr satelitów i wykonania pozostaje własnością COMBO; katalog wiedzy nie zastępuje systemowej prawdy.

**Ważne:** dokumenty w tym katalogu są nowymi notatkami historycznymi. Wersja wtyczki, nazwa brancha czy dawny stan z raportu nie dowodzą obecnego wdrożenia, sprzedaży ani dostępności.
