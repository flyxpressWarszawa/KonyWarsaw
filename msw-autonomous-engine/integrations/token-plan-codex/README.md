# MSW — oficjalna konfiguracja Token Plan dla Codex

Ten katalog zawiera **bezkluczowy profil dla interaktywnego narzędzia Codex CLI**.
Nie jest nowym silnikiem MSW, nie uruchamia agenta w tle i nie zastępuje
`msw-central-engine-worker` na Renderze.

## Dostawcy Alibaba — nie mieszaj kluczy

- **Token Plan**: `https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`;
  wymaga *dedykowanego* klucza Token Plan. Właściwy do obsługiwanych narzędzi
  programistycznych (Codex, Hermes Agent), a nie do dowolnych skryptów i backendów.
- **Model Studio Pay-as-you-go**: endpoint `https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`;
  wymaga odpowiedniego klucza dla workspace oraz aktywacji usługi. Ten kanał
  jest przeznaczony m.in. dla istniejącego własnego backendu MSW.

## Połączenie Codex CLI na maszynie, na której naprawdę działa Codex

1. Przed użyciem wymień tokeny ujawnione w rozmowie i pliku. Nigdy nie dodawaj
   kluczy do GitHub, kodu, screenów ani logów.
2. Zainstaluj Codex CLI z oficjalnych źródeł (Node.js 18+ i `@openai/codex`)
   na własnym komputerze albo na przeznaczonym do tego autoryzowanym środowisku
   interaktywnych narzędzi programistycznych.
3. Połącz zawartość `config.toml` z lokalnym `~/.codex/config.toml` bez usuwania
   istniejących profili i ustawień.
4. Ustaw prywatny sekret **w środowisku procesu Codex**, pod nazwą
   `MSW_ALIYUN_TOKEN_PLAN_API_KEY`. Nie ustawiaj go jako `OPENAI_API_KEY` w
   istniejącym serwisie MSW — ten identyfikator może być już używany.
5. Sprawdź wersję CLI i uruchom ręczny test w narzędziu:
   `codex --version`, potem `codex` i testowe „Cześć”.
   Sprawdź w konsoli Token Plan limit oraz zużycie.

Opcjonalnie: oficjalny **Bailian CLI** obsługuje
`bl config agent --agent codex --region ap-southeast-1 --key <ENCODED_TOKEN_PLAN_KEY> --model qwen3.8-max`.
Wymaga uruchomienia na **tej samej maszynie**, na której jest Codex.
Parametr `--key` przyjmuje zakodowany klucz `o1_`, a nie nazwę modelu.
Nie wklejaj oryginalnego klucza do historii powłoki.

## Granica odpowiedzialności

Obecnie Render uruchamia `python -m msw_engine`, a **nie** Codex CLI.
Samo dodanie tego pliku do repozytorium nie podłącza Codex ani nie uruchamia
Token Plan na Renderze. Aby tam działał Codex, potrzebny jest osobny
autoryzowany host/runner z zainstalowanym Codex, prywatną konfiguracją
i dozwolonym interaktywnym sposobem użytkowania.

Według warunków Alibaba Token Plan jest **wyłącznie** do używania z
obsługiwanymi interaktywnymi narzędziami programistycznymi lub agentami
OpenClaw. Nie kieruj przez ten klucz masowego generowania ofert, cyklicznych
zadań, prywatnych danych klientów ani niestandardowego backendu Python MSW.

Źródła:
- https://www.alibabacloud.com/help/en/model-studio/codex
- https://www.alibabacloud.com/help/en/model-studio/cli/config
- https://www.alibabacloud.com/help/en/model-studio/more-tools
- https://www.alibabacloud.com/help/en/model-studio/token-plan-personal-overview.md
