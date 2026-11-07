# Wykonawca jednego zadania MSW — stan HOLD

Nowy workflow wiąże jedno wskazane zadanie z kolejki GitHuba z zakresem kodu,
raportem walidacji, dokładnym commitem i późniejszym odczytem pull requestu.
**W tej wersji wykonanie i publikacja pozostają zablokowane.**
`execution_gate.py` zawsze zwraca `HELD`,
`reason=execution_backend_not_integrated`, `execution_permitted=false`
i `publication_permitted=false`. Nie ma jeszcze podłączonego wykonawcy
wydającego uprawnienie do realizacji zadania.

Ustawienie `MSW_CODING_TASK_EXECUTOR_ENABLED=true` pozwala wejść do przygotowania
kontekstu przy spełnieniu warunków repozytorium i gałęzi `main`. Ta zmienna,
treść zadania, dostęp do GitHuba i poprawny hash nie odblokowują modelu.
Dotychczasowy błąd Copilota `model_not_supported` pozostaje nierozwiązany;
konfiguracja modelu w pliku workflow nie jest potwierdzeniem dostępu do niego.

Źródłem jest
[`.github/workflows/msw-coding-task.md`](../.github/workflows/msw-coding-task.md).
GitHub Actions uruchamia jego skompilowany plik `.lock.yml`; zapis źródła ani
udana kompilacja nie dowodzą wykonania zadania. Nowy workflow ma wyłącznie
ręczne `workflow_dispatch`. Nie dodaje harmonogramu, nie zwalnia produkcyjnego
HOLD i nie zmienia działania istniejących kolejek.

## Powtarzalna kompilacja

CI odtwarza lock oficjalnym kompilatorem `gh-aw` v1.0.1, którego pobrany plik
sprawdza po rozmiarze i SHA-256. Kompilacja używa `--action-mode release`,
`--action-tag 0627fdbe4beb5f3b71aa569f8d97150b87f2111a` i `--no-check-update`.
Przypięcie tej samej akcji znajduje się w wejściowym cache kompilatora
`.github/aw/actions-lock.json`. Dzięki temu odtworzenie nie zależy od obecności
programu `gh` ani od dostępu do API podczas rozwiązywania tego przypięcia.

Lock jest wyłącznie wynikiem kompilatora. Nie usuwa się z niego komentarzy ani
nie edytuje go ręcznie; CI wymaga pełnej zgodności bajtów przez `git diff`.
Rutynowe odtwarzanie korzysta z zapisanego cache bez
`--force-refresh-action-pins`, które celowo wymusza ponowne rozwiązywanie.

## Wejście i zakres

Wywołanie wskazuje trzy publiczne wartości, pochodzące z uprzednio
zarejestrowanego zadania:

| Wejście | Co identyfikuje |
| --- | --- |
| `task_id` | Dokładne ID zadania: 32 małe znaki szesnastkowe |
| `queue_commit` | Pełny 40-znakowy SHA commita zawierającego plik zadania |
| `file_sha256` | SHA-256 wszystkich bajtów kanonicznego pliku zadania, razem z końcowym LF |

`file_sha256` różni się od `request_sha256`. Hash żądania obejmuje kanoniczny
obiekt bez pola `request_sha256`. Hash pliku obejmuje cały obiekt z tym polem
oraz końcowy znak nowej linii. Weryfikator pliku z PR #3 używa hasha całego
pliku; takie potwierdzenie nadal nie jest uprawnieniem do kodowania.

Repozytorium `flyxpressWarszawa/KonyWarsaw`, kolejka `msw/coding-queue`
i ścieżka `msw-coding-tasks/<task_id>.json` są ustalone przez kod.
Zadanie nie może wybrać adresu API, repozytorium, polecenia ani polityki.
Baza kodu pochodzi z `github.sha` zaufanego uruchomienia na `main`.

Ten wykonawca obsługuje wyłącznie cztery zakresy z istniejącego katalogu
[`targets.json`](../msw-coding-bridge/targets.json) i polityki kontrolera:

| Target | Dozwolone ścieżki na `main` |
| --- | --- |
| `technical-core` | `msw-ai-technical-core/` |
| `portal` | `msw-ai-centrum-robotow/app/`, `msw-ai-centrum-robotow/tests/` |
| `compute-adapters` | `msw-compute-node-adapters/` |
| `research` | `msw-research-engine/` |

`runtime`, `owned-llm` i `wordpress` otrzymują
`unsupported_executor_target`. Mają inne gałęzie albo wymagają najpierw
autoryzowanego pozyskania źródeł. Ogólny dostęp do repozytorium nie rozszerza
zakresu konkretnego zadania. Także wewnątrz dozwolonych katalogów obowiązują
ograniczenia kontrolera: chronione pliki wdrożeń, zależności, sekrety,
niebezpieczne typy plików oraz istniejące testy pozostają poza zmianą.

## Przygotowanie kontekstu i zatrzymanie przed modelem

Job `coding_task` korzysta z zaufanej rewizji workflow i uprawnienia tylko do
odczytu repozytorium. `task_context.py` pobiera commit, wpisy drzewa i blob
zadania, sprawdza tożsamość blobu Git oraz hash wszystkich bajtów pliku.
Następnie stosuje ten sam walidator `msw.coding-task.v1`, którego używa most.
Weryfikuje dokładne kodowanie kanoniczne, ID, hash żądania, publiczny charakter
danych i zarejestrowany zakres. Odczyt aktualnej referencji kolejki i, gdy trzeba,
porównanie historii potwierdzają, że wskazany commit należy do kolejki.

Wynik `msw.coding-task-context.v1` zapisuje oryginalne zadanie, jego tożsamość,
`queue_commit`, zaobserwowany `queue_head_sha`, `base_sha`, target i ścieżki.
To zapis obserwacji z przygotowania. Nie zastępuje późniejszego sprawdzenia
STOP/HOLD ani przyjęcia zadania przez centralę.

Plik `context.json` ma postać kanonicznego UTF-8 JSON z jednym końcowym LF.
Job oblicza SHA-256 dokładnych bajtów i przekazuje go jako własny output
`context_sha256`. Kolejne etapy dostają ten hash osobno jako
`EXPECTED_CONTEXT_SHA256` i wymagają jawnego
`--expected-context-sha256`. Kandydat nie może wybrać innego kontekstu ani
uznać własnego hasha za zaufane przypięcie. Po potencjalnej pracy modelu
workflow ponownie pobiera oryginalny artifact przygotowania.

Kontekst zawiera `execution_authorized=false` i `production_hold=true`.
`execution_gate.py` tworzy publiczny zapis `HELD` i wyjścia workflow z obiema
zgodami ustawionymi na `false`. Warunki jobów blokują aktywację, agenta,
wykrywanie zagrożeń, publikację i odczyt wyniku. Dodatkowe kontrole przed
agentem i przed publikacją wymagają tych samych zaufanych wyjść. W obecnej
wersji poprawne przygotowanie kończy się więc materiałem do przeglądu,
bez wywołania modelu i bez nowego PR od tego workflow.

## Powiązanie kandydata z zadaniem

Poniższa ścieżka jest przygotowana do późniejszej integracji wykonawcy.
Samo jej istnienie nie zwalnia HOLD.

Model otrzyma zweryfikowany kontekst jako dane wymagań oraz dokładny blok
tożsamości do umieszczenia w opisie PR. Treść tytułu, celu i kryteriów
akceptacji nie staje się instrukcją systemową ani poleceniem do uruchomienia.
Zakres pozostaje zawężony do jednego targetu. Jedyna poprawna nazwa gałęzi
to `msw-coding-task/<task_id>`, bez dodatkowego przyrostka.

Workflow zachowuje kontroler, politykę, walidatory, klienta mostu i katalog
targetów z bazowego SHA. Po przygotowaniu poprawki uruchamia ten kontroler
w środowisku testów bez odziedziczonych sekretów i w osobnej przestrzeni sieci.
Rejestr kontrolera wybiera konkretne testy, w tym każdy nowy test; istniejące
testy nie mogą zostać zmienione ani usunięte.

`task_result.py bind` otrzymuje oryginalny kontekst z zewnętrznym hashem,
raport `controller.validate` i podstawowy dowód `commit/tree`. Następnie
niezależnie od opisu modelu sprawdza:

- dokładny HEAD, drzewo Git i pojedynczego rodzica równego `base_sha`;
- dokładną gałąź zadania oraz czysty index i worktree;
- pełny zestaw dodanych, zmienionych i usuniętych ścieżek z porównania drzew;
- zawężenie do targetu, politykę plików, limity i niezmienność istniejących testów;
- zgodność bajtów plików z blobami commita i hashami w raporcie;
- komplet wybranych testów, ich stałe polecenia, katalogi, poprawne zakończenie,
  wymagane liczniki wykonania oraz hashe wyjścia.

Wynik `msw.coding-task-proof.v1` wiąże context hash, tożsamość zadania i kolejki,
base SHA, branch, commit, tree, ścieżki, Git blob SHA, SHA-256 treści i dowody
testów. `validation_sha256` obejmuje dokładne bajty przekazanego raportu.
Dowód musi pochodzić z zaufanego etapu walidacji; sam hash kontekstu nie jest
podpisem raportu ani pozwoleniem na publikację.

Blok `msw.coding-task-binding.v1` powstaje przez
`expected_pr_binding(context, pin)`. Znaczniki
`<!-- msw-coding-task-binding:start -->` i
`<!-- msw-coding-task-binding:end -->` otaczają jedną linię kanonicznego JSON.
Blok zawiera hash kontekstu, repozytorium, oba SHA kolejki, task path, task ID,
request ID, request/file hashes, target, bazę i ścieżki. Commit kandydata
znajduje się w dowodzie walidacji; nie jest potrzebny do przygotowania bloku
przed rozpoczęciem pracy. W opisie PR musi wystąpić dokładnie jeden identyczny
blok, ze znacznikami w osobnych liniach.

## Ochrona publikacji i niezależny odczyt PR

Oddzielny etap `safe_outputs` najpierw wymaga uprawnień wykonania i publikacji
z zaufanego jobu. W tej wersji oba pozostają `false`.
`task_publication.cjs` jest odtwarzany z bazowego SHA. Porównuje propozycję
`create_pull_request` z artifactem dowodu: repozytorium, target, dokładny blok
opisu, gałąź i bazę, a także prawdziwy commit, pojedynczego rodzica i tree
w pakiecie Git. Sprawdza pełny diff pakietu i tożsamość blobów. Podmieniony
pakiet, inny opis zadania, dodatkowe pliki albo inna baza zatrzymują publikację.
Sam guard weryfikuje dane; zapis PR należy do istniejącego mechanizmu
`safe-outputs`.

Po ewentualnej przyszłej publikacji job `readback` dostaje tylko uprawnienia
odczytu i numer faktycznie utworzonego PR. `task_result.py verify-pr` ponownie
wymaga pierwotnego context hash oraz dowodu kandydata. Wykonuje wyłącznie
ograniczone GET do ustalonego repozytorium GitHuba:

1. Odczytuje rzeczywisty PR: numer, stan otwarty i niescalony, ID i nazwę
   repozytorium po obu stronach, branch, head SHA, base branch i base SHA,
   pojedynczy dokładny blok zadania oraz liczbę zmienionych plików.
2. Odczytuje commit i potwierdza tree oraz jedynego rodzica.
3. Odczytuje pełną listę plików i porównuje każdą ścieżkę, status i Git blob SHA.
   Rozpoznany przez GitHuba rename musi odpowiadać sprawdzonym zmianom
   usunięcia starej ścieżki i dodania nowej.
4. Pobiera bloby i ponownie liczy Git blob SHA oraz SHA-256 treści, z limitami
   odpowiedzi i łącznego rozmiaru.
5. Ponownie odczytuje PR, aby wykryć zmianę jego tożsamości lub opisu podczas
   sprawdzania.

Zmiana `base.sha` od czasu przygotowania zwraca
`stale_pr_base_rebase_retest_required`. Potrzebne są ponowne przygotowanie bazy,
rebase i testy; stary PASS nie jest przenoszony na nową bazę. Odczyt nie tworzy
commita, nie aktualizuje PR, nie scala i nie wysyła ACK do centrali.

## Znaczenie wyników

| Wynik | Co potwierdza |
| --- | --- |
| `CONTEXT_READY` | Zweryfikowany plik zadania, obserwację jego przynależności do kolejki i dopuszczalny zakres danych |
| `HELD` | Przygotowanie zostało zatrzymane przed wykonaniem z powodu braku zintegrowanego backendu |
| `PASS` w task proof | Powiązanie konkretnego lokalnego commita z zadaniem i zaufanym raportem testów |
| `PROPOSED_VERIFIED` | Zgodność odczytanego, nadal otwartego PR z przypiętym zadaniem i dowodem kandydata |
| `BLOCKED` | Niespełniony warunek; publiczny, stały kod przyczyny bez tokenów i treści błędu z serwera |

`PROPOSED_VERIFIED` zachowuje `central_ack_verified=false`,
`acceptance_verified=false`, `merged_verified=false`, `deployed_verified=false`
i `execution_authorized=false`. Przejście testów i powiązanie PR z zadaniem
nie dowodzą jeszcze spełnienia wszystkich jego wymagań znaczeniowych.
Wynik nie potwierdza centralnego ACK, scalenia, wdrożenia Rendera, zmiany
WordPressa ani wykonania kodu przez Python HUB.

## Pozostała granica integracji

Przed odblokowaniem potrzebny jest uzgodniony backend wykonania z przyjęciem
zadania, jednorazowym uprawnieniem przypiętym do zadania i konkretnego przebiegu
oraz świeżym sprawdzaniem Hermes STOP/HOLD i grantu. Musi być określone,
jak uprawnienie wygasa, jest zużywane i jak centrala przyjmuje wynik bez
powielania istniejących kolejek. Nie dodano alternatywnego schedulera ani
wymyślonego endpointu produkcyjnego.

Osobnym warunkiem pozostaje izolacja systemu plików walidacji. Odtworzenie
walidatorów z bazy, prawa `444`, sprawdzanie ich hashów, czyste środowisko
i izolacja sieci nie tworzą pełnej granicy uprawnień między uruchamianym kodem
testów a plikami dowodów tego samego użytkownika. Trzeba zapewnić ochronę
zaufanych walidatorów i wyników przed zapisem przez kandydata, zanim wykonawca
uzyska zgodę na uruchamianie niezaufanego kodu i publikację.

Ta zmiana przygotowuje sprawdzalny przepływ danych i odmowę bez zgody.
Nie instaluje pakietu WordPress, nie restartuje usług Rendera, nie zmienia
ich konfiguracji i nie scala gałęzi uruchamiającej wdrożenie.
