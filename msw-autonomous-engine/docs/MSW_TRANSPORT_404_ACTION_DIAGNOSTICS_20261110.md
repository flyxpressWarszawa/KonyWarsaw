# MSW — diagnostyka przyczyny HTTP 404 workera Render

Status: DRAFT / przegląd kodu; bez wdrożenia, bez migracji bazy.

## Potwierdzone 10 listopada 2026
- Główny worker Render srv-dbltdb9srm7s73ec0v00 (gałąź msw/engine-runtime-20261104) okresowo zwraca server_rejected_request z HTTP 404.
- W tym samym czasie jego osobna ścieżka AI poprawnie zapisuje i weryfikuje ACK dla llm.generate oraz wykonywane są knowledge.ingest i combo.analytics.
- Ostatni potwierdzony module.execute completed pochodzi z 2026-11-08T00:00:18Z. Te zadania nie odzyskały normalnego tempa.
- WordPress RemoteStore::receipt może zwracać HTTP 404, jeżeli nie znajduje przypisanego potwierdzenia zadania. HTTP 404 może też pochodzić z serwera WWW, proxy lub błędnego adresu. Stary log nie rozróżnia sytuacji.
- Backup produkcyjnej bazy i test odtworzenia nie są potwierdzone. Nie zmieniać indeksów lub uprawnień w celu sprawdzenia hipotezy.

## Zmiana tylko diagnostyczna
- Oznacz istniejący wyjątek HTTPError w kliencie podpisanym etykietą operacji (claim, heartbeat, complete, status).
- Rejestruj etykietę tylko przy HTTP 404 w istniejącym zdarzeniu connection_retry.
- Nigdy nie zapisuj treści żądań, odpowiedzi, tokenów, sekretów, nonce ani adresów URL.
- Nie dodawaj żądań HTTP, nie zwiększaj częstotliwości, nie zmieniaj retry, lease, ACK ani kontraktu kolejki.
- Próbne testy jednostkowe symulują HTTP 404 bez kontaktu z produkcją.

## Interpretacja po odrębnie zatwierdzonym wdrożeniu
- claim: sprawdź dokładną trasę transportu, firewalla/WAF i walidację payloadu. To nie dowodzi istnienia błędnego adresu.
- status: sprawdź tylko odczytowo istnienie zadania, przypisanie node, stan oraz sumę kontrolną. Brak receipt NIE jest ACK.
- complete: sprawdź istnienie lease i rzeczywisty zapis wyniku bez ponownego wykonywania callbacków.
- heartbeat: sprawdź trasę i konkretną dzierżawę.
- unknown: brak wystarczających przesłanek; bez automatycznych napraw.

## Warunki wyjścia z DRAFT
1. Zaliczone testy izolowane dla Client.send i Worker.loop z symulowanym HTTPError.
2. Review, czy log zawiera wyłącznie dozwolone metadane.
3. Wdrożenie wyłącznie po kontroli okna przeciążenia i z planem rollback; nie scalamy automatycznie.
4. Zapisz pierwsze rzeczywiste zdarzenie oznaczone action, następnie napraw konkretny etap w nowym PR.

Uwaga: ta poprawka nie usuwa HTTP 404 i nie zmniejsza CPU sama z siebie.

## Wynik lokalnych testów izolowanych — 10.11.2026
- Własny offline harness z dokładnym kodem `Client.send` pobranym z gałęzi PR i wyodrębnioną ścieżką `Worker.loop` przeszedł `6/6` testów (`python -m unittest discover`).
- Zasymulowano HTTP 404 na czterech etapach, HTTP 401, błąd sieci, odfiltrowanie nieznanej etykiety i zachowanie poprzedniego formatu logu przy HTTP 503.
- Użyto atrapy `protected_open` i wyłącznie mockowanych odpowiedzi. **Nie wykonano testów na całym repozytorium ani CI GitHub Actions.**
- Potwierdzono brak zmian parametrów lease, ACK i retry w różnicy plików `client.py` i `runner.py`.
- Dalsza blokada: brak informacji o etapie rzeczywistego 404 przed wdrożeniem PR, dlatego brak pozwolenia na masowe ponowienia `module.execute`.

## Potwierdzenie CI w pełnym repozytorium — 10.11.2026
- Workflow GitHub Actions `msw-render-404-diagnostic-tests.yml` dla gałęzi PR #15: https://github.com/flyxpressWarszawa/KonyWarsaw/actions/runs/42845268078.
- Stan: **completed / success**. Test `python3 -m py_compile` oraz `python3 -m unittest discover -s tests -p 'test_transport_404_action_diagnostics.py' -v` wykonane na pełnym checkout repozytorium.
- Wynik: **6 testów / 6 zaliczonych**, potwierdzony w logu job `isolated-unit-tests`.
- Pozostaje brak testu integracyjnego na żywym Render. Samo CI nie ustala, czy HTTP 404 powstaje przy `claim`, `status`, `heartbeat` czy `complete`.
- Żaden test CI nie wykonał żądania do produkcyjnego serwera, nie zmienił lease, ACK, PHP WordPressa ani bazy.
