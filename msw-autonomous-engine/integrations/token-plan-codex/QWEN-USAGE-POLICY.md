# MSW Qwen Token Plan — umiarkowane zużycie, listopad 2026–luty 2027

## Co zostało ograniczone

W launcherze **Connect-MSW-Qwen.ps1** ustawiamy dla *interaktywnego* Qwen:
- `model_reasoning_effort="medium"`, bez zmiany domyślnego GPT-6 Astra.
- Wyłączenie niestabilnego `mcp_servers.node_repl` **tylko w sesji Qwen**, aby
  ograniczyć błędy i zbędne definicje narzędzi; Codex zachowuje wbudowane
  narzędzia kodowania, a inne dotychczasowe profile pozostają bez zmian.
- GitHub clone z `--depth 1 --filter=blob:none --sparse`: bez pobierania
  całej historii i niepowiązanych katalogów repozytorium.
- Brak `codex exec` w CRON, bez pętli, bez automatycznych płatnych zapytań.
- Jedno konkretne zadanie w sesji; unikaj wklejania pełnych logów,
  wielotysięcznych plików, wielokrotnych niepotrzebnych prób i trybu xhigh.

## Limity i rytm

Na ekranie użytkownika 10 listopada 2026 widnieje **Token Plan Personal
Standard** ważny do **11 lutego 2027**. Według zaktualizowanego 9 listopada
2026 FAQ Standard ma obecnie **80 000 Credits/miesiąc**. To nie jest liczba
tokenów: zużycie Credits zależy od modelu, trybu myślenia, narzędzi i innych
czynników. Niewykorzystany limit miesięczny nie przechodzi na następny okres.

Progi operacyjne, nie serwerowy twardy limit:
- `60%` miesięcznego limitu: przejrzyj zadania, skróć kontekst i pytania.
- `80%`: tylko pilne interaktywne naprawy, bez ciężkich analiz.
- `90%`: wstrzymaj kosztowne sesje i korzystaj z dostępnego GPT lub
  czekaj na kolejne przyznanie miesięcznej puli. Nie kupuj dodatków automatycznie.

Sprawdzaj **Usage / Plan Quota** w osobistym panelu Alibaba Cloud. Oficjalny
Bailian CLI (jeżeli jest zainstalowany i zalogowany) udostępnia również
tylko do odczytu `bl usage token-plan --output json`, które zwraca procent
wykorzystania i datę resetu. Jest to odczyt danych administracyjnych,
**nie** wywołanie generowania tekstu. Nie zapisuj kluczy ani całych wyników
z identyfikatorami konta w publicznym repozytorium.

W ChatGPT włączono przypomnienie o kontroli co dwa dni około 19:00, do
11 lutego 2027; to NIE jest potwierdzony dostęp do prywatnego licznika.
Nie ma jeszcze autonomicznego odczytu Usage z konta Alibaba przez ChatGPT.

## Gdy Git nie działa na Windows

Polecenie `git --version` zwracające `CommandNotFoundException` oznacza
brak Git for Windows lub brak jego katalogu w PATH. Zainstaluj jednorazowo:
`winget install --id Git.Git -e --source winget` (lub z oficjalnej
strony https://git-scm.com/install/windows), następnie **nowe** PowerShell
i `git --version`. Samo `git clone` nie zużywa Token Plan Credits,
ale pobiera kod źródłowy z GitHub.

## Źródła (2026-11-10)
- https://docs.modelstudio.console.alibabacloud.com/en/model-studio/token-plan-personal-faq
- https://www.alibabacloud.com/help/en/model-studio/token-plan-personal-overview.md
- https://www.alibabacloud.com/help/en/model-studio/cli/usage-quota.md
- https://git-scm.com/install/windows
