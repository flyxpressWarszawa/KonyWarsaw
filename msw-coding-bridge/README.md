# MSW Coding Bridge

**Stan: `PROPOSED_NOT_AGREED_NOT_DEPLOYED`. Transport produkcyjny pozostaje na HOLD.**
Kod przygotowuje trwałe połączenie istniejącej kolejki MSW z repozytorium
`flyxpressWarszawa/KonyWarsaw`. Nie instaluje modelu ani nie uruchamia samodzielnego
kodowania przez sam fakt dodania workflow. Aktualną granicę admission, STOP/HOLD
i potwierdzeń opisuje [CONTRACT.md](CONTRACT.md).

ChatGPT korzysta ze swoich obecnych połączeń GitHub i MSW Direct Control. Render
zachowuje istniejące gałęzie źródłowe. Python HUB korzysta z obecnego adaptera MSW;
jego backend wymaga autoryzowanego eksportu źródeł przed zmianą kodu. Etykieta
`source_engine` nie stanowi uwierzytelnienia ani zgody na wykonanie polecenia.

## Przebieg po uzgodnieniu kontraktu i aktywacji

1. Uprawniony użytkownik lub moduł zgłasza publiczne wymaganie przez `coding-submit`
   lub `coding.submit`. Istniejący Central Store przyjmuje `adapter.run` i nadaje
   natywny identyfikator zadania. Nie powstaje drugi planista wykonawczy.
2. Callback rejestru sprawdza właściciela, argumenty i aktywny lease zadania,
   następnie zapisuje trwałą projekcję transportową. GitHub może ją przejąć
   dopiero po potwierdzeniu ukończenia natywnego zadania i zgodności polityki.
3. Zaufany workflow GitHub Actions przedstawia krótkotrwały token OIDC. Przejmuje
   najwyżej jeden rekord i zapisuje dane na osobnej gałęzi `msw/coding-queue`.
4. Klient odczytuje plik z konkretnego commitu. WordPress niezależnie sprawdza
   dokładne bajty, identyfikator, hash, lease i run, zanim zapisze ACK.
5. Uprawniony wykonawca może przygotować zmianę kodu na właściwej gałęzi, uruchomić
   testy i otworzyć PR. Merge i wdrożenie pozostają osobnymi, weryfikowanymi etapami.

`delivered` potwierdza dostarczenie danych. Nie oznacza wykonania kodu, testów ani
wdrożenia aplikacji. Raport klienta jawnie zawiera `code_executed:false` oraz
`production_changed:false`.

## Elementy projektu

| Element | Rola |
| --- | --- |
| [targets.json](targets.json) | Siedem obszarów kodu i ich rzeczywiste gałęzie |
| [client.py](client.py) | OIDC, claim, niezmienny zapis i potwierdzenie odbioru |
| [package.py](package.py) | Odtwarzalny ZIP z sumą kontrolną, bez instalacji |
| `wordpress/msw-github-coding-bridge/` | Rozszerzenie obecnego MSW Direct i Central Engine |
| `tests/python/` | Testy kontraktu, transportu, konfliktów i niejednoznacznych wyników |
| `tests/php/run.php` | Testy WordPress admission, OIDC i projekcji transportowej |
| `tests/fixtures/task-utf8.json` | Wspólne bajty i skróty dla PHP/Pythona |
| `../.github/workflows/msw-coding-bridge.yml` | CI i domyślnie wyłączony transport produkcyjny |

Po instalacji pakiet dodaje narzędzia MSW Direct `coding-status`, `coding-submit`,
`coding-task`, `coding-connect` oraz capabilities `coding.submit`, `coding.status`,
`coding.result`. To opis przygotowanego interfejsu, nie dowód instalacji.
REST używa przestrzeni `/wp-json/msw-github-coding/v1` na centrali MSW.

| Target | Gałąź | Zakres |
| --- | --- | --- |
| `technical-core` | `main` | `msw-ai-technical-core/` |
| `portal` | `main` | Aplikacja i testy `msw-ai-centrum-robotow/` |
| `compute-adapters` | `main` | `msw-compute-node-adapters/` |
| `research` | `main` | `msw-research-engine/` |
| `runtime` | `msw/engine-runtime-20261104` | `msw-autonomous-engine/` |
| `owned-llm` | `msw/local-llm-20261031` | `msw-owned-llm/` |
| `wordpress` | Zależna od konkretnego rozszerzenia | Wymagany autoryzowany eksport nieobecnych źródeł |

Etykiety `github_agent_scope` i `chatgpt_branch_worker` opisują ścieżki obsługi.
Nie potwierdzają dostępności modelu, jego uprawnień ani działającego autonomicznego
wykonawcy. `msw/coding-queue` jest rejestrem danych: nie należy wykonywać jego
plików ani scalać go z gałęzią aplikacji.

## Zaufanie, tożsamość i ponowienia

OIDC wiąże wymianę z repozytorium o ID `405190198`, właścicielem `90477347`,
gałęzią `refs/heads/main` i dokładnym workflow:

```text
flyxpressWarszawa/KonyWarsaw/.github/workflows/msw-coding-bridge.yml@refs/heads/main
```

Audience to `https://mobilny-serwis-warszawa.pl/msw-github-coding`. Klient używa
krótkotrwałych tokenów GitHub Actions. Nie kopiuje poświadczeń ChatGPT, nie wymaga
globalnego PAT i nie zapisuje tokenów na dysku. Żądania HTTPS mają limit czasu
i rozmiaru odpowiedzi; przekierowania i proxy z otoczenia procesu są wyłączone.

Repozytorium jest publiczne. Każde zgłoszenie wymaga literalnego `public_data:true`.
Dane klientów, CRM, prywatne wiadomości, logi i klucze nie należą do tego kanału.
Blokowanie znanych wzorców poświadczeń stanowi dodatkową kontrolę, nie gwarancję
rozpoznania każdego sekretu lub prywatnego tekstu.

Tożsamość zadania to `SHA256(source_engine + ":" + request_id)[:32]`.
Kanoniczny JSON ma sortowane klucze, UTF-8 bez dodatkowych spacji i bez uciekania
Unicode, slashy oraz U+2028/U+2029. Plik kończy się jednym LF. Powtórzenie tej
samej tożsamości z inną treścią powoduje konflikt. Klient nigdy nie przekazuje
parametru nadpisania `sha` do PUT istniejącego pliku.

Po timeout zapisu klient nie wysyła drugiego PUT w ciemno. Konflikt 409/422
rozstrzyga przez odczyt. Ponowienie ACK jest ograniczone do jednej dodatkowej
próby po błędzie sieci lub HTTP 503 i zachowuje identyczny lease oraz zadanie.

| Stan | Rzeczywiste znaczenie |
| --- | --- |
| `held` | Zaufane połączenie odpowiada, lecz transport jest wstrzymany |
| `admitted` | Istnieje natywne zadanie admission w Central Store |
| `recorded` | Istnieje powiązana projekcja transportowa |
| `leased` | Rekord należy chwilowo do konkretnego workflow run |
| `idle` | Brak kwalifikującego się rekordu do dostarczenia |
| `write_unconfirmed` | Wynik zapisu do GitHuba jest niejednoznaczny |
| `stored_unacknowledged` | Znany commit zawiera plik, lecz ACK nie został potwierdzony |
| `delivered` | WordPress potwierdził zgodny odczyt zadania z commitu |
| `blocked` | Warunek wstępny lub weryfikacja nie pozwoliły ukończyć operacji |

## PR #3: weryfikacja pliku przez workera

[PR #3](https://github.com/flyxpressWarszawa/KonyWarsaw/pull/3) dodaje osobny,
domyślnie wyłączony weryfikator GitHub w runtime Rendera. Klient mostu przekazuje
w publicznym raporcie dwa różne skróty:

- `request_sha256`: skrót kanonicznego obiektu zadania bez samego pola skrótu;
- `file_sha256`: skrót całego pliku, z polem `request_sha256` i końcowym LF.

Pole `expected_sha256` weryfikatora pliku musi otrzymać **`file_sha256`**.
Adapter `coding.verify_delivery` sprawdza ponadto tożsamość i wewnętrzny skrót
zadania. Dopasowanie pliku do commitu nadal nie potwierdza uprawnień wykonawcy,
testów, merge ani wdrożenia. Handler w kodzie workera nie nadaje automatycznie
capability uwierzytelnionej kolejce WordPressa.

## Weryfikacja lokalna i paczka

Wymagane: Python 3.12+ oraz PHP 8.3 z OpenSSL. Klient używa biblioteki standardowej.
Testy korzystają ze sztucznych odpowiedzi API i nie wymagają produkcyjnych tokenów,
WordPressa, modelu AI ani połączenia z siecią.

```bash
python -m unittest discover -s msw-coding-bridge/tests/python -v
php -n msw-coding-bridge/tests/php/run.php
python msw-coding-bridge/package.py --output-dir /tmp/msw-coding-package
```

Testy sprawdzają rzeczywiste reguły kodu, ale są dowodem offline, nie zdalnym ACK.
Wspólny fixture zawiera polskie litery, emoji i separatory Unicode; testy obejmują
podmianę hash/ID, konflikty zapisu, błędne lease, tożsamość workflow, niezgodny ACK,
niejednoznaczny zapis oraz brak tokenów w publicznym raporcie.

CI udostępnia ZIP i sumy kontrolne jako artifact
`msw-coding-bridge-package-<commit>-<attempt>` przez 14 dni. Źródła pozostają w
GitHubie. Utworzenie lub pobranie paczki nie instaluje pluginu i nie zwalnia HOLD.

## Późniejsza aktywacja

Obowiązuje aktualne wstrzymanie zmian produkcyjnych. Najpierw trzeba uzgodnić
wspólny kontrakt, admission, globalny STOP/HOLD i rzeczywiste granty. Wtyczka
ma transport wyłączony domyślnie; samo `coding-connect` nie zwalnia blokady.
Workflow wymaga niezależnie `MSW_CODING_BRIDGE_ENABLED == 'true'`.

Po autoryzacji fazy produkcyjnej należy zainstalować dokładnie sprawdzony pakiet,
odczytać jego wersję i rejestr capabilities, sprawdzić natywną kolejkę i dopiero
wtedy uruchomić ograniczony test połączenia. Oddzielnie sprawdzić wynik kodowania,
testy, PR i każde uzgodnione wdrożenie. Main może automatycznie wdrażać Technical
Core, więc merge jest zmianą produkcyjną. Nie uruchamiać konkurencyjnego buildu.

Włączony workflow obsługuje `probe` i `deliver`; harmonogram ma minuty `:17` i `:47`
każdej godziny UTC, a faktyczny start może być opóźniony. Jeden run dostarcza
najwyżej jedno zadanie. Trwałe dowody to zadanie w repozytorium i powiązany ACK;
sam `probe` nie potwierdza działania pełnej ścieżki zapisu.

GitHub Copilot pozostaje oddzielnie wstrzymany po `model_not_supported`. Ten most
nie zmienia planu ani uprawnień do modeli. Jego trwałość nie zależy od miesięcznego
okna workflow Copilota.

## Pozostająca integracja wykonawcy kodowania

Obecny `msw-github-engine/controller.py context` przekazuje statyczne priorytety
i rejestr testów. Workflow `msw-github-engine.md` oraz jego skompilowany lock nie
konsumują jeszcze `msw.coding-task.v1` z `msw/coding-queue`. Włączenie samego
transportu nie zmieni tego zachowania. Publiczny manifest także nie jest
wykonawcą ani potwierdzeniem dostępności modelu.

Pełna integracja wymaga przekazania jednego zweryfikowanego zadania do rzeczywistego
promptu wykonawcy oraz powiązania wyniku z `task_id`, `request_sha256`, commitem
źródłowym kolejki, commitem zmiany, testami i konkretnym PR. Zakończenie trzeba
potwierdzić osobnym odczytem; dane zadania nie mogą wybierać nowych uprawnień,
poleceń powłoki ani obejść istniejącej polityki. Wygenerowanego locka nie należy
ręcznie zmieniać bez odpowiadającego źródła workflow i walidacji kompilatorem.

Istniejący automatyczny wykonawca ma zakres czterech obszarów na `main`.
`runtime` i `owned-llm` mają odrębne gałęzie, a brakujące źródła WordPressa i
Python HUB wymagają autoryzowanego eksportu. Te różnice trzeba obsłużyć w
wykonawcy przed ogłoszeniem samoczynnego kodowania wszystkich silników.
Aktualny pakiet i oba PR-y pozostają w fazie przygotowania pod produkcyjnym HOLD.

## Dokumentacja mechanizmów

- [GitHub OIDC](https://docs.github.com/en/actions/reference/security/oidc)
- [Wersje REST API](https://docs.github.com/en/rest/about-the-rest-api/api-versions)
- [Zapis plików w repozytorium](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents)
- [Harmonogram GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

Klient jawnie korzysta ze wspieranej wersji REST API `2026-03-10`.
