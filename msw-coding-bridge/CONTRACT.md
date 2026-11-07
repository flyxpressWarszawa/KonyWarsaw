# Wspólny kontrakt kodowania MSW — propozycja v1

**PROPOSED_NOT_AGREED_NOT_DEPLOYED. Transport produkcyjny: HOLD.**

Repozytorium `flyxpressWarszawa/KonyWarsaw` stanowi wspólne źródło kodu.
ChatGPT korzysta z istniejących uwierzytelnionych konektorów GitHub i MSW Direct,
Render z obecnych gałęzi repozytorium, a Python HUB z istniejącego adaptera MSW.
Backend Python HUB wymaga autoryzowanego eksportu źródeł przed jego zmianą.

## Wspólna kolejka i granica Hermesa

Propozycja `MSW-HERMES-BRIDGE-1-DRAFT-2` / `msw.hermes.bridge.v1` zachowuje jedną
autorytatywną granicę admission, job_id, lease/fence, heartbeat, STOP/HOLD oraz
niezależny odczyt efektu. Najnowsze testy inertnego konsumenta są dowodem offline;
nie dowodzą istnienia gotowego backendu, grantów ani działającej inferencji.

Ten adapter przyjmuje zgłoszenia przez istniejący Central Store:
`adapter.run`, capability `coding.submit`. Callback sprawdza natywne zadanie,
właściciela, argumenty i aktywny lease, następnie zapisuje trwałą projekcję
transportową. GitHub może przejąć ją dopiero po potwierdzeniu ukończenia natywnego
zadania i ponownej kontroli polityki. Nie powstaje drugi wykonawca poleceń,
harmonogram WordPress ani nowy rodzaj w zamkniętej liście RemoteStore.

Domyślny HOLD blokuje przyjmowanie i transport. `coding-connect` wiąże jedynie
capability; jawne `transport_enabled:true` jest oddzielną przyszłą operacją.
Mapowanie globalnego STOP Hermesa wymaga uzgodnionego, rzeczywiście dostępnego
interfejsu. Ten kod nie ogłasza takiego mapowania jako wdrożonego.

## Format zadania

Zadanie `msw.coding-task.v1` zawiera dokładnie pola:

| Pole | Reguła |
| --- | --- |
| `schema` | `msw.coding-task.v1` |
| `id` | Pierwsze 32 znaki SHA-256 z UTF-8 `source_engine + ":" + request_id` |
| `request_id` | 8–128 znaków `A-Za-z0-9_.:-`; identyczny przy ponowieniu |
| `source_engine` | Etykieta autoryzowanego źródła; nie zastępuje uwierzytelnienia |
| `target` | Jeden z siedmiu obszarów w `targets.json` |
| `title` | 1–120 znaków Unicode, nie same białe znaki |
| `objective` | 1–4000 znaków Unicode |
| `acceptance` | 1–8 kryteriów, każde 1–400 znaków |
| `public_data` | Literalne `true`; repozytorium jest publiczne |
| `created_at` | UTC `YYYY-MM-DDTHH:MM:SSZ` |
| `request_sha256` | SHA-256 kanonicznego obiektu bez tego pola |

Kanoniczny JSON: UTF-8, rekurencyjnie sortowane klucze, zachowana kolejność tablic,
bez spacji, escaping Unicode/slashy, NaN i Infinity. U+2028/U+2029 pozostają
literalne. Cały plik ma jeden końcowy LF i najwyżej 16384 bajty. Ten sam ID z
inną treścią powoduje konflikt. Wspólny fixture sprawdza PHP i Pythona, polskie
litery, emoji i separatory. Dane klientów, prywatne logi, klucze i CRM są poza
tym publicznym transportem. Wykrywanie wzorców sekretów jest kontrolą dodatkową.

## OIDC i transport

GitHub Actions używa krótkotrwałego OIDC: issuer
`https://token.actions.githubusercontent.com`, audience
`https://mobilny-serwis-warszawa.pl/msw-github-coding`, repository_id `405190198`,
owner_id `90477347`, ref `refs/heads/main`, dokładny workflow:

`flyxpressWarszawa/KonyWarsaw/.github/workflows/msw-coding-bridge.yml@refs/heads/main`

WordPress sprawdza podpis RS256, stały JWKS, czas, repozytorium i workflow.
Wymiana `/wp-json/msw-github-coding/v1/exchange` używa envelope
`msw.coding-exchange.v1`, `ok:true`, zweryfikowanego `run_id`.
Kroki: `probe` → `claim` jednego zadania → niezmienny zapis i odczyt GitHub → `ack`.
Lease ma losowy token, termin i powiązanie z konkretnym run. ACK musi odpowiadać
task_id, request_sha256, lease, run, commit_sha, task_path, repozytorium i gałęzi.
WordPress niezależnie odczytuje dokładne bajty pliku z danego commitu.

Pliki trafiają wyłącznie do `msw-coding-tasks/<id>.json` na `msw/coding-queue`.
Konflikt zapisu rozstrzyga odczyt identycznego pliku, nigdy nadpisanie. Timeout
zapisu pozostaje UNKNOWN. Po znanym zapisie i niepotwierdzonym ACK stan to
`stored_unacknowledged`. Zadanie nie wybiera hosta, ścieżki ani repozytorium.

## Włączenie PR #3 do wspólnego silnika

[PR #3](https://github.com/flyxpressWarszawa/KonyWarsaw/pull/3) dodaje do istniejącego
workera opt-in `github.verify_file`. Weryfikuje commit, kolejne drzewa i typ pliku,
Git blob oraz SHA-256 bajtów. To oddzielny odczyt dowodowy dla tego mostu.

**Dwa skróty mają różne znaczenie:** `request_sha256` obejmuje obiekt bez pola
skrótu. `file_sha256` obejmuje cały zapisany plik, łącznie z tym polem i LF.
Do `expected_sha256` weryfikatora PR #3 należy przekazać `file_sha256`.
Nie wolno zamieniać tych wartości ani uznać zgodności jednej za dowód drugiej.

Adapter weryfikacji dostarczenia sprawdza również treść zadania, jego schema,
deterministyczny ID i request_sha256. Wspólny wynik ma powiązanie z żądaniem,
plikiem i commitem. Obsługa w workerze pozostaje za jawnym przełącznikiem.
Rejestracja handlera w kodzie nie nadaje capability uwierzytelnionej kolejce
WordPressa. Zgodność payloadu i lokalny test nie stanowią zdalnego ACK.

| Stan / dowód | Co rzeczywiście potwierdza |
| --- | --- |
| admitted | Natywne zadanie Central Store i jego tożsamość |
| recorded | Projekcja transportowa powiązana z tym zadaniem |
| delivered | Niezależny odczyt zgodnych bajtów zadania z commitu |
| github_file_verified | Tożsamość pliku w konkretnym commicie |
| task_identity_verified | Zgodność wewnętrznego formatu, ID i skrótu zadania |
| proposed / merged | Istniejący PR / faktyczny wynik merge |
| tests_verified / deployed_verified | Osobne dowody testów / live efektu; domyślnie false |

## Bieżąca dostawa i aktywacja

Kod powstaje na izolowanej gałęzi z CI. WordPress HOLD i zmienna GitHub
`MSW_CODING_BRIDGE_ENABLED` są domyślnie wyłączone. Pakiet ZIP i SHA-256 służą
do przeglądu; ich utworzenie nie instaluje ani nie aktywuje wtyczki.

Przed fazą produkcyjną trzeba uzgodnić admission, globalny STOP/HOLD, rzeczywiste
granty i świeży odczyt wyniku. Zachować obecne kolejki, COMBO, Photo Guard,
sekrety i gałęzie. Technical Core na `main` ma automatyczny deploy: merge tego
PR byłby zmianą produkcyjną. Nie uruchamiać kolejnej kopii trwającego buildu Rendera.

Copilot pozostaje oddzielnie wstrzymany po `model_not_supported`. Ten adapter
nie naprawia dostępu do modelu i nie pobiera nowych kluczy. ChatGPT może kodować
przez istniejący konektor GitHub; samoczynne kodowanie na serwerze wymaga osobno
potwierdzonego wykonawcy i uprawnień. Połączenie nie jest ograniczone do miesiąca.
