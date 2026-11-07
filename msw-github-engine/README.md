# MSW GitHub Engine

Silnik rozwoju kodu MSW uruchamiany w GitHub Actions. Korzysta z GitHub Copilot
przez GitHub Agentic Workflows. Pracuje na publicznym kodzie istniejącego
repozytorium, przygotowuje małą poprawkę, sprawdza ją rzeczywistymi testami
i przekazuje ją jako pull request z dowodami wykonania.

- [Przebiegi silnika](https://github.com/flyxpressWarszawa/KonyWarsaw/actions/workflows/msw-github-engine.lock.yml)
- [Kontrola testów](https://github.com/flyxpressWarszawa/KonyWarsaw/actions/workflows/msw-github-engine-ci.yml)
- [Przygotowane zmiany](https://github.com/flyxpressWarszawa/KonyWarsaw/pulls)

## Zakres pracy

| Element | Odpowiedzialność |
| --- | --- |
| GitHub Engine | Rozpoznanie problemu w kodzie, poprawka, testy i pull request |
| Technical Core, portal, adaptery, Research Engine | Kod dostępny do rozwoju na `main` |
| Centralny worker Render | Istniejący wykonawca zadań WordPress/COMBO na `msw/engine-runtime-20261104` |
| WordPress i COMBO | Dotychczasowe uwierzytelnione wykonanie oraz potwierdzenia zmian na stronach |

Zmiana w `main` nie jest potwierdzeniem wdrożenia centralnego workera Render.
Technical Core ma własne istniejące wdrożenie powiązane z `main`.
Wynik testów lokalnych nie jest potwierdzeniem pracy satelitów.

## Działanie

### Dostęp do automatycznego kodowania

Test połączenia z 7 listopada 2026 przeszedł przygotowanie i testy, ale usługa
Copilot odrzuciła zarówno wybór automatyczny, jak i jawny `gpt-6.1-sol` błędem
`model_not_supported`. [Dokładny przebieg diagnostyczny](https://github.com/flyxpressWarszawa/KonyWarsaw/actions/runs/42396484831)
nie utworzył poprawki ani pull requestu. Automatyczne wywołania Copilota są
domyślnie wstrzymane, aby nie powtarzać znanego błędu. Po potwierdzeniu dostępu
zmienna repozytorium `MSW_GITHUB_ENGINE_ENABLED=true` włącza cykle.

Połączenie ChatGPT–GitHub, kontrola testów oraz konfiguracja mostu zadań MSW nie
wymagają włączenia tej zmiennej. Dostęp do repozytorium nie jest potwierdzeniem
uprawnienia do modelu.

Źródłem automatyzacji jest `.github/workflows/msw-github-engine.md`.
Plik `.lock.yml` jest jego kompilacją przez `github/gh-aw` 1.0.1. GitHub
uruchamia skompilowany workflow; sam Markdown nie stanowi uruchomienia.

Cykl godzinowy i ręczny wybiera jeden konkretny problem. Istniejący otwarty
pull request z prefiksem `[MSW GitHub Engine]` zatrzymuje kolejne propozycje,
aby nie tworzyć wielokrotnie tej samej pracy. Agent musi wykazać problem,
przygotować ograniczoną zmianę i przejść lokalną walidację. Gdy nie znajdzie
uzasadnionej poprawki, zapisuje brak zmiany zamiast tworzyć sztuczny commit.

Domyślne okno pracy AI: od 7 listopada 2026 do **7 grudnia 2026, 00:00 UTC+1**
(koniec wyłączny). Jest to ustawione okno działania, nie odczyt daty końca
abonamentu. Workflow nie kupuje planu, nie odnawia subskrypcji i nie zmienia
limitów rozliczeń konta. Dostęp Copilota rozstrzyga rzeczywiste uwierzytelnienie
wykonania; sam dostęp do repozytorium nie dowodzi aktywnego pakietu Copilot.

Zmienna repozytorium `MSW_GITHUB_ENGINE_ENABLED=0` zatrzymuje następne cykle.
Zatrzymanie workflow w Actions jest dodatkowym dostępnym wyłącznikiem.
Terminy i reguły walidacji znajdują się w `policy.json` oraz źródle workflow.
Ich aktualizacja wymaga spójnej zmiany i ponownej kompilacji.

Harmonogram Actions jest obsługiwany przez GitHub. Uruchomienia mogą być
opóźnione; konfiguracja godzinowa nie jest gwarancją wykonania co 60 minut.

## Dostęp i weryfikacja

Agent używa krótkotrwałego `GITHUB_TOKEN` z odczytem repozytorium i uprawnieniem
`copilot-requests: write`. Zapisy wykonuje oddzielny mechanizm `safe-outputs`.
Hasła WordPress, klucze OpenAI, sekrety COMBO, dane klientów i historia właściciela
nie są kopiowane do tego publicznego repozytorium ani do kontekstu agenta.

Kontroler porównuje propozycję z dokładnym bazowym SHA. Odrzuca zmiany poza
dozwolonymi katalogami, wdrożenia i sekrety, osłabienie istniejących testów,
niebezpieczne typy plików i przekroczenie rozmiaru poprawki. Testy dobiera
ze stałego rejestru poleceń. Nie wykonuje poleceń wygenerowanych w opisie PR.
Wynik i sumy SHA-256 zapisuje w lokalnym `.msw-github-engine/`.

Przed zapisem pull requestu osobna kontrola porównuje drzewo plików z rzeczywiście
przetestowanym drzewem. Publikowany pakiet musi zawierać tę samą poprawkę i tę samą
bazę. Rozbieżność zatrzymuje zapis. Raporty przebiegu są dostępne w Actions.

Workflow CI uruchamia kontroler oraz pełny zestaw testów przy zmianach i PR.
Pull request utworzony standardowym `GITHUB_TOKEN` może nie wywołać kolejnego
workflow; dlatego wymagane sprawdzenie propozycji odbywa się też w tym samym
uruchomieniu silnika przed publikacją wyniku. Otwarcie PR nie oznacza scalenia
ani publikacji na produkcji.

## Testy

Istniejący zestaw obejmuje Node API Technical Core, adaptery obliczeniowe,
dwa lokalne testy integracyjne, parser publicznych źródeł, reguły portalu
i dziewięć testów API portalu. Poprzedni workflow nie uruchamiał tych testów API.
Nowy CI uruchamia je przez pytest wraz z izolowaną bazą SQLite.

Weryfikacja wdrożeniowa obejmuje 27 testów kontrolera, 42 istniejące testy
aplikacji oraz 29 sprawdzeń integracyjnych. Każdy kolejny przebieg zapisuje
własny aktualny wynik; te liczby opisują zestaw początkowy.

Wymagane środowisko: Python 3.12, Node 24 oraz przypięte zależności testowe:

```bash
python -m pip install -r msw-github-engine/requirements-ci.txt
python -m unittest discover -s msw-github-engine/tests -v
python msw-github-engine/controller.py baseline
python msw-github-engine/controller.py gate
python msw-github-engine/controller.py context
python msw-github-engine/controller.py validate --base <pelny-bazowy-SHA>
```

Raporty są dowodami konkretnego przebiegu i konkretnej rewizji. Nie są uczeniem
wag modelu. Nieudany test, odmowa dostępu, brak zależności lub niepewny wynik
pozostają tak opisane; nie są zamieniane na status sukcesu.

## Oficjalna dokumentacja

- [Copilot CLI w GitHub Actions i rozliczenia](https://docs.github.com/en/copilot/concepts/agent-capabilities/agent-powered-workflows-and-operations/copilot-cli-in-github-actions)
- [Uwierzytelnianie wbudowanym GITHUB_TOKEN](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli-in-actions)
- [GitHub Agentic Workflows](https://github.github.com/gh-aw/)
- [Źródło kompilatora 1.0.1](https://github.com/github/gh-aw/tree/0627fdbe4beb5f3b71aa569f8d97150b87f2111a)

GitHub Models został wycofany 30 lipca 2026; ten silnik korzysta z aktywnej
ścieżki Copilot, a nie z wycofanego API Models.
