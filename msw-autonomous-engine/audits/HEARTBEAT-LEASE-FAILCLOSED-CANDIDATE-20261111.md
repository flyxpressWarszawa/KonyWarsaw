# MSW — kandydat Heartbeat / Lease / HTTP, bez wdrożenia

Stan: **DRAFT / HOLD.** Kontynuacja audytu Qwen z 10–11.11.2026.
To nie jest akceptacja pełnego połączenia Hermes, migracja bazy,
zmiana STOP/HOLD ani autoryzacja płatnych wywołań.

## Zakres wprowadzony w tej gałęzi

1. `PaidAttemptGuard` przed procesem potomnym dla `llm.generate`:
   atomowe i trwałe zarezerwowanie identyfikatora zlecenia lub
   `request_id` (jeżeli jest poprawny). Przy niepewnym wyniku, restarcie
   lub nowym lease **brak drugiego płatnego wywołania**; zamiast tego
   `paid_attempt_outcome_unverified`. Nie ma automatycznego resetu
   rezerwacji. Hash ID i skrót payloadu nie zawierają treści użytkownika.
2. `runner.Worker.execute`: jeśli w chwili błędu heartbeat dziecko ma
   **już gotowy** wynik w IPC, wynik jest zapisywany w istniejącym
   `Journal` pod pierwotnym lease. Nie oznacza to akceptacji przez
   serwer: odrębne `complete/status` i hash readback nadal decydują.
   Jeśli wynik nie jest gotowy, worker fail-closed zatrzymuje dziecko,
   a płatna próba pozostaje oznaczona jako niepewna.
3. `client.Client`: `OriginPaused.retry_after` jest zachowany w
   `TransportError` bez wywołania serwera. HTTP `OpenerDirector`
   jest izolowany **per-thread** (WordPress podpisy HMAC nadal per request).
4. Testy: tylko syntetyczne obiekty i mocked transport; bez płatnych API,
   bez sieci, bez wykonywania subprocessów w nowych testach.

## Czego to nie rozwiązuje / twarde granice

- Długość i sposób przedłużania `lease` w **aktualnym WordPress
  `msw-unified-engine/transport.php`** nie zostały potwierdzone
  aktualnym odczytem źródła. Nie ma zgody na kontynuowanie płatnej pracy
  po niezweryfikowanej utracie heartbeat.
- `PaidAttemptGuard` obejmuje tę drogę `llm.generate`, nie
  wszystkie pozostałe ścieżki płatnego API, wp cron, routery, Hermes,
  Hunter, gateway czy bezpośrednie wywołania Render. Nie wolno ogłaszać
  globalnej ochrony przed duplikatami ani wyłączać STOP/HOLD.
- Istniejące semantyki `Journal` i ACK/readback nie są zmieniane.
  Powtórzenie **zweryfikowanego, zapisanego** wyniku nie wykonuje
  ponownie inferencji; próba bez zapisanego wyniku pozostaje HOLD.
- Nie ma wdrożenia ani zmiany źródła produkcji; nie wprowadzono
  nowego workera, kolejki, harmonogramu ani klucza.
- `PaidAttemptGuard` tworzy nową lokalną bazę SQLite w MSW_DATA_DIR;
  akceptacja na rzeczywistym współdzielonym nośniku i migracja
  dopiero po osobnym przeglądzie backup/rollback oraz wymaganych
  uprawnieniach. Nie modyfikować bramki 600 MiB.
- B05/B06/B08 i obecne blokady polityki pozostają nieruszone.

## Wymagania do ewentualnego przyszłego scalenia

Źródłowo potwierdzić aktualny PHP lease TTL/extend/status,
zakres job-id/request-id w całej flocie, paid-path coverage, izolację
wątków i zachowanie SQLite po restarcie. Uruchomić niezależne
testy integracyjne HTTP/WP/PHP w nieprodukcyjnym środowisku,
w tym 409/404, timeout i utratę ACK, a potem review bezpieczeństwa.
CI offline PASS jest tylko dowodem działania izolowanych testów.
