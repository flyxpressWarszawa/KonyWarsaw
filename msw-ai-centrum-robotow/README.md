# MSW AI — Centrum Robotów i Analityki

**Samodzielna aplikacja bez WordPressa.** Backend: FastAPI, SQLAlchemy, PostgreSQL. Frontend: responsywny interfejs HTML/CSS/JS bez WP, bez build-stepu, bez fałszywych wskaźników.

## Co zrobiono

- Premium dashboard robotów, 5 KPI, rzeczywiste wykresy ruchu, dostawców i botów.
- Rejestracja zewnętrznych robotów, indywidualny token agenta pokazywany **jeden raz**.
- Autoryzowane raporty agentów, API do obserwacji ruchu gateway, katalog, porównywarka, CSV.
- Endpointy stanu własnej aplikacji i bazy. Dane z dwóch źródeł są oznaczone oddzielnie.
- Bez formularzy usterek, wyceny napraw, diagnozy zdjęć i diagnostyki WordPressa.
- Brak zależności od WordPress API, PHP, REST czy wtyczek WordPressa.

**Ważne:** Pomiar User-Agent oznacza deklarację nazwy klienta, nie potwierdzenie tożsamości. Ruch HTTP i opóźnienia nie są rankingiem inteligencji ani mocy obliczeniowej modelu. Zgłoszenia samych agentów są opisane jako self-reported. Żadnych danych demonstracyjnych przedstawianych jako LIVE.

## Lokalnie

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# bez DATABASE_URL używa ./msw_robots.db tylko do developmentu
export INGEST_SECRET="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Otwórz `http://localhost:8000/msw-ai-centrum-robotow/`. Zbiór danych pozostanie pusty, dopóki nie nadejdą prawdziwe zdarzenia.

## Wdrożenie Render

1. Umieść cały katalog w repozytorium Git (np. GitHub). Nie dodawaj `.env` ani kluczy API.
2. W **zatwierdzonej przez właściciela** przestrzeni Render utwórz Blueprint na podstawie `render.yaml`. Zakłada bazę PostgreSQL i niezależną usługę aplikacyjną w regionie Frankfurt.
3. Weryfikacja: `https://<render-host>/msw-ai-centrum-robotow/api/v1/health` musi zwrócić `ok:true, wordpress_dependency:false`.
4. Dla zachowania **dotychczasowego adresu** `https://mobilny-serwis-warszawa.pl/msw-ai-centrum-robotow/` skonfiguruj `edge/cloudflare-worker.js` na **jednej ścieżce**. W Worker dodaj `MSW_ROBOT_ORIGIN` = prawdziwy adres usługi Render (z HTTPS). W razie braku Cloudflare potrzebna równoważna reguła reverse proxy na serwerze brzegowym.
5. Ustaw Worker Route `mobilny-serwis-warszawa.pl/msw-ai-centrum-robotow*`. Przed włączeniem przetestuj nową usługę na adresie Render i wyłącz cache dla API. **Nie** ruszaj głównego DNS i nie usuwaj WordPressa używanego przez resztę MSW.
6. Po przełączeniu sprawdź nagłówek odpowiedzi `X-MSW-App: robot-intelligence-independent`, panel, `/api/v1/health`, rejestrację robota, wysyłkę jego zdarzenia, porównywarkę i eksport CSV. Po weryfikacji możesz wyłączyć jedynie stary widget/panel Robotów w WordPressie.

Poddomeny `arena.`, `gielda.`, `roboty.` są wpisane w menu, ale ich DNS/usługi nie są tworzone przez ten pakiet; adresy należy aktywować niezależnie. Portal nie twierdzi, że te moduły już działają.

## API (wszystkie ścieżki względne do `/msw-ai-centrum-robotow`)

| Endpoint | Metoda | Uprawnienia | Działanie |
|---|---|---|---|
| `/api/v1/health` | GET | publiczny | Status HTTP + DB |
| `/api/v1/overview` | GET | publiczny | KPI, czas, boty, dostawcy |
| `/api/v1/agents` | GET | publiczny | Katalog i liczba zdarzeń |
| `/api/v1/agents/register` | POST | publiczny, limit 5/h | Tworzy agenta, zwraca **jednorazowy** token |
| `/api/v1/agents/{id}/events` | POST | Bearer token agenta | Raport własny agenta |
| `/api/v1/events/ingest` | POST | Bearer `INGEST_SECRET` | Zdarzenie gateway z rozpoznanym User-Agent |
| `/api/v1/comparison?a=<uuid>&b=<uuid>` | GET | publiczny | Zestawienie dwóch agentów |
| `/api/v1/export.csv` | GET | publiczny | CSV obserwacji (do 30 dni) |
| `/api/v1/system` | GET | publiczny | Status niezależnych komponentów |
| `/api/v1/bots/catalog` | GET | publiczny | Obsługiwane wzorce crawlerów |

### Przykłady

```bash
curl -X POST https://TWOJ-HOST/msw-ai-centrum-robotow/api/v1/agents/register \
  -H 'content-type: application/json' \
  -d '{"name":"Research Agent","provider":"Moja firma","description":"Agent badawczy"}'

curl -X POST https://TWOJ-HOST/msw-ai-centrum-robotow/api/v1/agents/AGENT_ID/events \
  -H 'content-type: application/json' -H 'authorization: Bearer TOKEN_OD_REJESTRACJI' \
  -d '{"category":"task","latency_ms":340,"http_status":200}'

curl -X POST https://TWOJ-HOST/msw-ai-centrum-robotow/api/v1/events/ingest \
  -H 'content-type: application/json' -H 'authorization: Bearer TAJNY_KLUCZ_GATEWAY' \
  -d '{"user_agent":"GPTBot/1.0","http_status":200,"latency_ms":90}'
```

**Zbieranie danych:** Cloudflare Worker w tym pakiecie służy tylko do przekierowania ścieżki, nie przechwytuje automatycznie całego ruchu głównej witryny. Pomiary typu `gateway` pojawiają się dopiero po podłączeniu źródła wywołującego uwierzytelniony endpoint ingest (np. osobny proces przetwarzania logów). Unikaj przesyłania IP, ciasteczek, danych formularzy i identyfikatorów użytkowników. To nie jest narzędzie szpiegowania odwiedzających.

**Bezpieczeństwo:** Token agenta jest przechowywany wyłącznie jako SHA-256, a jawny egzemplarz pokazany tylko przy rejestracji. Endpoint podany przez agenta pozostaje daną tekstową; backend nigdy nie wykonuje połączeń do niezaufanych adresów (ochrona przed SSRF). Prawdziwe wyniki weryfikacji modelu wymagają odrębnego, niezależnego benchmarku i nie są tu symulowane. Uruchom limity oraz dodatkową antyspamową ochronę WAF/Turnstile przed szeroką publiczną rejestracją; limity w pamięci procesu nie zastępują rozproszonego systemu rate limiting.

## Etap migracji

Ten pakiet jest kompletnym kodem gotowym do wdrożenia i lokalnych testów, **nie dowodem wdrożenia**. Na stronie dotychczas działa WordPress do chwili skonfigurowania routingu u operatora domeny. Bez potwierdzenia docelowego workspace Render i bez dostępu do dostawcy proxy nie należy twierdzić, że domena została przełączona.

## MSW AI Centrum Robotów v2 (24.10.2026)

- Nowy hero premium inspirowany futurystycznym ekosystemem Warszawy (oryginalny zasób klienta), w zasobach własnej aplikacji.
- Menu: giełda, roboty na żywo, zewnętrzne, dołączające, agenci, porównania, moc AI, API i raporty.
- Wyświetlanie tylko prawdziwych statystyk; nie tworzymy fikcyjnych indeksów mocy czy kursów.
- Interaktywny wykres zdarzeń 1H / 6H / 24H / 7D, linie i słupki, endpoint /api/v1/timeseries.
- Interaktywna rejestracja i lista ostatnio dołączających agentów, tokeny jednorazowe.
- Render: środowisko `Robot`, projekt `msw-ai-centrum-robotow`, baza PostgreSQL w Frankfurt (`msw-ai-roboty-db`); darmowa baza wygasa 2026-11-23. Do pracy produkcyjnej wymagana podłączona DATABASE_URL oraz trwały plan.
- Do utrzymania tego samego URL potrzebny reverse proxy CDN na ścieżce `/msw-ai-centrum-robotow/*`.
