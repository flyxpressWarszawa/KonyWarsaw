# Połączenie lokalnego Codex/Qwen z kodem MSW na GitHub

**Co zostaje połączone:** działający na Windows Codex CLI (profil `aliyun`,
Qwen 3.8 Max z wykupionego Token Plan) pracuje interaktywnie na osobnej lokalnej
kopii źródeł `flyxpressWarszawa/KonyWarsaw`. Zmiany kodu trafiają z tej kopii
do GitHub wyłącznie przez świadomą operację Git/PR. Docelowy worker działa
na Renderze z gałęzi `msw/engine-runtime-20261104`.

**Czego to nie robi:** nie podłącza tokenu do automatycznego serwisu,
nie tworzy nowego workera, nie wdraża zmian na Render, nie zapisuje kluczy
ani nie wykonuje cyklicznych płatnych wywołań. Dla general-purpose backendu
trzeba osobnego, autoryzowanego API Pay-as-you-go.

## Jednorazowo na Windows

1. Na tym komputerze Codex i profil `aliyun` są już skonfigurowane;
   nie trzeba modyfikować dotychczasowego `gpt-6-astra` ani wtyczek.
   Upewnij się, że klucz Token Plan został wymieniony po wcześniejszym
   ujawnieniu i zapisany prywatnie jako
   `MSW_ALIYUN_TOKEN_PLAN_API_KEY` (zaczyna się od `sk-sp-`).
2. Sprawdź, czy Git jest zainstalowany: `git --version`.
   Jeśli nie, zainstaluj Git for Windows z https://git-scm.com/download/win.
3. Pobierz `Connect-MSW-Qwen.ps1` z bieżącego folderu w GitHub.
   W pobranym pliku można obejrzeć wszystkie polecenia; **nie** jest
   wymagane podawanie klucza do skryptu.
4. Otwórz nowe PowerShell i uruchom polecenie (jeżeli plik zapisano
   w `Downloads`):
   `& "$HOME\Downloads\Connect-MSW-Qwen.ps1"`.
   Jeżeli system blokuje podpis pobranego skryptu, po zweryfikowaniu
   zawartości użyj `Unblock-File "$HOME\Downloads\Connect-MSW-Qwen.ps1"`
   i uruchom ponownie. Nie uruchamiaj losowych zdalnych skryptów przez `iex`.
5. Skrypt pobiera repozytorium do `$HOME\MSW-Qwen\KonyWarsaw` i otwiera
   interaktywny Codex w `msw-autonomous-engine` na lokalnej gałęzi
   `msw/codex-qwen-local-20261110`.
   Powinien pokazać: profil skonfigurowany, właściwe repozytorium,
   osobną gałąź i dopiero potem interaktywny Codex.

## Pierwsza wiadomość w Codex (wpisz ręcznie)

> Przeczytaj AGENTS.md i README.md. Zbadaj wyłącznie źródła
> msw-autonomous-engine. Zaproponuj jedną małą, bezpieczną poprawkę i testy.
> Nie zmieniaj sekretów, WordPressa ani produkcji Render. Na razie tylko analiza.

Pracę zaczynaj od analizy i testów. Każde zapisane przez Codex usprawnienie
najpierw sprawdź w lokalnym Git, następnie poprzez osobną gałąź / Pull Request.
Nie wypychaj w ciemno nowych zmian do produkcyjnej gałęzi.

## Następna aktualizacja MSW przez GitHub/Render

Z lokalnego PowerShell, **dopiero po przejrzeniu zmian i testów**:

```powershell
git status --short
git diff --check
git diff
```

Jeśli później chcesz przenieść zmiany do GitHub, użyj osobnego PR kierowanego
na `msw/engine-runtime-20261104` i uruchom CI. Po scaleniu wykonaj
kontrolowany deploy na istniejącym workerze Render, zweryfikuj jego stan
`LIVE`, logi, działanie kolejki i podpisane ACK. Sama lokalna odpowiedź
Qwen nie potwierdza wdrożenia na Renderze.

**Zasady Alibaba:** osobisty Token Plan jest tylko do interaktywnych
narzędzi programistycznych; zabronione są automatyczne skrypty, backendy
oraz cykliczne zadania. Źródło:
https://www.alibabacloud.com/help/en/model-studio/token-plan-personal-overview.md
