# MSW Hermes — migracja backlogu WordPress → prywatne archiwum GitHub → Render

**Stan: projekt testowany lokalnie, produkcja bez zmian (13.11.2026).**

## Dlaczego nie przenosimy po prostu rekordów przez publiczny GitHub
- W centralnej bazie `ovse_mswue_remote_jobs` było **11 525 oczekujących zadań** przy ostatnim odczycie; zdecydowana większość to `module.execute`, który ostatecznie uruchamia natywną funkcję PHP WordPress.
- Istniejący Hermes działa na Renderze i pobiera zadania z WordPressa przez podpisany transport HMAC oraz protokół `claim → lease → complete → readback ACK`.
- **GitHub nie jest bazą z atomową dzierżawą**, więc samo skopiowanie rekordów nie jest równoznaczne z przeniesieniem wykonania ani nie daje ochrony przed dublem.
- Repo `flyxpressWarszawa/KonyWarsaw` jest publiczne. Nie wolno do niego wysyłać jawnych payloadów, sekretów, danych klientów ani wykonywalnych poleceń bez kontroli.
- Drugi znany prywatny repozytorium `signal-harbor-companion` obsługuje aplikację Lovable; nie nadaje mu się automatycznie roli archiwum zadań.

## Nowa ścieżka
1. **WordPress:** oddzielny moduł eksportu tylko do odczytu, wyłączony domyślnie; wyłącznie sparowany główny węzeł Hermesa. HMAC i ochrona nonce z istniejącego `RemoteStore::authorise('status')`; maksymalnie 12 wpisów na stronę; paginacja kluczem czasu+UUID.
2. **Render:** weryfikuje każdy payload przez SHA256, zapisuje dane w prywatnym SQLite z AES-256-GCM; przechowuje kursory oraz testuje ponowny odczyt.
3. **Prywatny GitHub:** zaszyfrowane strony zadań, opatrzone kontrolną sumą Git blob, po eksporcie sprawdzane przez ponowny odczyt. Brak potwierdzenia GitHub = brak przesunięcia kursora.
4. **Hermes:** operacyjna kolejka i jej podpisane ACK pozostają w WordPressie do rzeczywistego przeniesienia konkretnego typu zadania; archiwum GitHub jest kopią do odzyskiwania i analiz, nie kolejką aktywnego wykonania.
5. **Inne źródła:** COMBO, satelity, Python HUB, Telegram i usługi Render wymagają osobnych, zatwierdzonych adapterów oraz reguł retencji i klasyfikacji. Nie kopiujemy ich na ślepo.

## Stworzone i przetestowane lokalnie
- `hermes_queue_mirror.py` — szyfrowanie, paginacja, checkpoint, weryfikacja prywatności repozytorium, kod GitHub Contents API i logika ponawiania.
- `msw-hermes-queue-export.php` — eksporter wp-json oparty na istniejącym kluczu HMAC.
- 14/14 testów jednostkowych (testowe stuby HTTP) PASS; `php -l` PASS.
- Odrzucana jest próba eksportu do repo publicznego, eksport bez klucza, źle podpisany payload, zmieniony SHA oraz przesunięcie kursora bez readback.

## Warunki bezpiecznego startu
1. Potwierdzenie **pełnego backupu i odtwarzalności** plików i bazy centrali oraz prywatnego dysku Rendera, z `receipts.sqlite3`.
2. Utworzenie **dedykowanego prywatnego repozytorium** na GitHubie, np. `flyxpressWarszawa/MSW-Hermes-Private-Queue` — to propozycja nazwy, repo **jeszcze nie istnieje**.
3. Podanie Renderowi przez jego panel osobnego krótkotrwałego tokenu GitHub App lub fine-grained PAT do tego jednego repo, oraz oddzielnego klucza szyfrującego 32 bajty. **Nigdy nie przesyłać tokenów do czatu ani plików GitHub**.
4. Bezpieczna instalacja eksportera w WordPressie (przełącznik OFF do testów), dopiero przy CPU <70% i braku HTTP 503. Jedna strona 12 rekordów co >=180 s, z backoff przy 429/503/504.
5. Potwierdzenie zgodności **rzeczywistego entrypointu Hermesa** `/var/data/msw-hermes/deployments/merge09-20261112/entrypoint.py`; aktualny Render używa kodu na dysku, więc sama publikacja commitu nie zmienia wykonawcy.
6. Pilot 12 wpisów -> SHA256 zgodny, zapis AES zweryfikowany, GitHub blob zweryfikowany, WordPress stany niezmienione -> stopniowe zwiększanie zakresu.
7. Reconciliacja działań natywnych per wtyczka przed przejściem ze sterowania na Renderze do faktycznych obliczeń w Pythonie.

## Odnotowane ograniczenia
- Eksport przenosi w pierwszej wersji identyfikator, typ, stan, znacznik czasu i oryginalny payload. Nie zawiera jeszcze powiązanego `wp_options` i pełnego `result`, więc nie jest autonomicznym odtworzeniem wszystkich operacji.
- Obserwacja paginowana nie jest transakcyjnym snapshotem zmian w czasie. Stany zawsze trzeba potwierdzić przed ponownym wykonaniem.
- **Liczba faktycznie przeniesionych zadań na GitHub w tym wdrożeniu: 0.** Pliki pakietu testowego są przygotowane, ale nie są zainstalowane produkcyjnie.
