# MSW — Qwen 3.8 Max pierwszy audyt (2026-11-10), weryfikacja źródeł

## Zakres i pochodzenie
- Raport lokalnego, interaktywnego Codex/Alibaba Qwen 3.8 Max pokazany przez właściciela.
- Niezależny odczyt źródeł z GitHub `flyxpressWarszawa/KonyWarsaw`,
  gałąź `msw/engine-runtime-20261104` (bez wdrażania lub płatnych zapytań).
- Wyniki lokalnego Qwen oparte na statycznej analizie; **Python nie był
  dostępny na jego Windows**, więc nie były to zakończone testy runtime.

## Potwierdzone w kodzie

1. **Heartbeat** (wysoki priorytet): `msw_engine/runner.py:191-207` wywołuje
   `self.client.send("heartbeat", ...)` bez lokalnego `except TransportError`.
   Jeśli wyjątek wystąpi, `finally` terminates active child and execution
   exits before `journal.save`. To dowodzi ścieżki utraty wyniku zadania,
   ale nie dowodzi, że doszło do utraty konkretnego płatnego zadania
   produkcyjnego. Ponowienie płatnego wywołania zależy również od serwera,
   lease, idempotency i journal; tego nie wolno zgadywać.

2. **Współdzielenie klienta**: `msw_engine/__main__.py:73-74` tworzy jednego
   `Client`, a `runner.py:237-266` przekazuje go kilku wątkom/lane'om.
   `client.py:55` przechowuje jednego `urllib.request.OpenerDirector`.
   Istnieje powierzchnia ryzyka współbieżności; faktyczny race condition,
   utrata ACK lub deadlock **nie są jeszcze udowodnione**. SQLite używa
   osobnych połączeń i WAL — nie deklarować, że samo to jest błędem.

3. **Zależność od POSIX**: `runner.py:68-74` importuje `resource`,
   `tests/test_shutdown.py:43` korzysta z `os.kill(..., SIGTERM)`.
   Lokalny Windows nie przejdzie wszystkich tych testów bez posixowego
   środowiska. Worker Render jest Linuxowy; ważniejsze jest pokrycie
   scenariuszy w CI Linux i niezależny test sandboxu.

4. **Zgubiona informacja cooldown**: `recovery.py:14-19` tworzy
   `OriginPaused(retry_after)` (dziedziczy `URLError`), lecz
   `client.py:76-77` zamienia URLError na `TransportError("transport_unavailable")`
   bez zachowania `retry_after`. Powoduje nieadekwatny backoff upstream.

## Testy offline — bez źródeł prywatnych
- `tests/test_qwen_audit_offline.py` używa wyłącznie fake transportu, fake
  child process i mock monotonic clock; nie uruchamia procesu roboczego,
  nie wysyła żądań HTTP ani nie wywołuje Alibaba/OpenAI.
- Jeden test odtwarza **obecne** zabicie procesu potomnego bez receipt
  przy błędzie heartbeat.
- Dwa kontrakty naprawy są oznaczone `@unittest.expectedFailure`.
  Dopóki nie ma zaakceptowanego projektu lease/timeout i polityki paid
  retry, nie udajemy, że błąd został już naprawiony.
- CI PASS z expectedFailure **nie jest dowodem naprawienia produkcji**.

## Bezpieczny plan kolejnych działań
1. Zdefiniować lease-expiry i receipt/idempotency semantics dla
   `heartbeat`, `claim`, `complete` i `status`, w tym brak cichego
   powtórnego wywołania płatnego modelu; dopiero wtedy proponować kod.
2. Dopisać izolowane testy konkurowania lane'ów i openera, bounded
   timeouts, cooldown oraz limitów systemowych w Linux CI.
3. Przejść niezależny review źródeł, testy i readback. Wdrożenie
   i włączenie płatnych providerów dopiero po autoryzacji oraz
   pozytywnych bramkach bezpieczeństwa.

**Stan na 2026-11-10: HOLD dla full-merge / Hermes do produkcji.**
Nic nie zmieniono w Render env, WordPress, Hermes, gateway, płatnych
wywołaniach ani kolejce produkcyjnej.
