# MSW AI — Alibaba / Hermes / ChatGPT: trwały monitoring, bez automatycznej inferencji Token Plan

**Stan:** wdrożenie testowe w repozytorium, bez włączania do produkcyjnego workera.
Współdziała z istniejącą kolejką Hermes → Render → WordPress, ale jej nie
modyfikuje. `msw-central-engine-worker` nadal startuje przez
`python -m msw_engine`, auto-deploy pozostaje wyłączony.

## Czego użytkownik już ma
- Alibaba Personal **Standard** w regionie Singapore, kupiony na trzy miesiące
  i zarejestrowany lokalnie w Windows Codex jako profil `aliyun`.
- Qwen 3.8 Max w interaktywnym Codex, rozumowanie `medium`.
- Podłączone do ChatGPT źródła MSW: Render, GitHub, MSW Direct Control.
- 3 zadania ChatGPT: alarmy silników co godzinę; quota Alibaba codziennie
  około 19:00; zweryfikowany postęp nauki codziennie około 20:00.

## Ważna granica licencji
Klucz osobistego Token Plan nie jest kluczem do autonomicznego backendu,
masowych wywołań ani dołączania do pełnej kolejki Hermes na Renderze.
Takie użycie może spowodować zawieszenie planu. Do automatycznych
wywołań Qwen potrzebny jest **oddzielny** Model Studio Pay-as-you-go,
autoryzowane, ograniczone środowisko i zamknięte bramki STOP/HOLD.
Wcześniejsze HTTP 403 `AccessDenied.Unpurchased` dla tej drogi NIE
zostało usunięte. Nie dokupuj nic automatycznie.

Oficjalne źródła:
- https://docs.modelstudio.console.alibabacloud.com/en/model-studio/token-plan-personal-faq
- https://www.alibabacloud.com/help/en/model-studio/token-plan-personal-overview.md
- https://www.alibabacloud.com/help/en/model-studio/cli/usage-quota
- https://www.alibabacloud.com/help/en/model-studio/activate-alibaba-cloud-model-studio

## Jak wykonać pierwszy bezpieczny odczyt rzeczywistego zużycia

`bl usage token-plan --output json` w oficjalnym Bailian CLI (gdy CLI ma
już poprawną, autoryzowaną sesję) zwraca `hasQuotaData`,
`per1MonthPercentage` (ułamek 0..1) i `per1MonthResetTime`
(czas w milisekundach). Jest to **odczyt statystyk**, nie wywołanie modelu.

Moduł `learning_quota_observer.py` NIE uruchamia CLI domyślnie.
Jawna opcja `--read-quota-cli` wywołuje tylko dokumentowane polecenie
usage. Odpowiedź jest normalizowana do kilku agregatów i nie zawiera
surowych danych konta, klucza ani prywatnych wiadomości.

Przykład działania po przygotowaniu prywatnego dostępu, bez drukowania
sekretów:
```
python -m telemetry.learning_quota_observer --read-quota-cli
```

W razie błędu lub braku logowania dostaniesz `NOT_CONNECTED`, a nie
fikcyjne 0%. Bezpieczny raport obejmuje procent wykorzystanej miesięcznej
puli, datę resetu i próg alarmowy. Standard po zmianie z 9 listopada
2026 to **80 000 Credits/miesiąc**, ale wyliczona liczba Credits jest
**szacunkiem na podstawie oficjalnego limitu i procentu**, a nie wyciągiem
z faktury. Ilość tokenów Codex NIE jest równa ilości Credits.

## Co musi się stać, aby pomiar działał 24/7 bez komputera użytkownika

1. Jednorazowo upoważnić dedykowane źródło **tylko do odczytu** statystyk
   w Alibaba. Dokumentacja wymienia role RAM
   `AliyunTokenPlanReadOnlyAccess` i `AliyunBSSReadOnlyAccess`, ale
   trzeba dodatkowo potwierdzić metodę uzyskania statystyk w konkretnym
   workspace i ważność autoryzacji CLI.
2. Sekret dostępu zapisać w prywatnym systemie sekretów obsługiwanego
   środowiska, nie w GitHub, logach ani rozmowie. Nie używać klucza
   Token Plan do inferencji z workera ani nie odświeżać dostępu przez
   nieautoryzowane logowanie automatyczne.
3. Na istniejącym **zaufanym** serwerze uruchomić wyłącznie odczyt statystyk,
   opublikować do ChatGPT przez autoryzowany, ograniczony adapter tylko
   **sanityzowane liczby**. Żaden publiczny endpoint bez uwierzytelnienia.
4. Dopiero przy rzeczywistym `hasQuotaData=true` i świeżej dacie
   przestawić status alarmu z `NOT_CONNECTED` na `VERIFIED_USAGE`.
   Sam kod w repozytorium nie oznacza aktywnej telemetrii i nie wykonuje
   ani jednego samodzielnego pomiaru z chmury.
5. Oddzielnie, po zatwierdzeniu i testach, podłączyć automatyczne
   `Pay-as-you-go` przez istniejący adapter i tę samą kolejkę Hermes.
   Brak tego połączenia nie blokuje interaktywnego Codex na Windows.

## Jak naprawdę liczyć postęp nauki MSW

Nie wolno nazywać wszystkich operacji 'trenowaniem modelu'.
Raporty ChatGPT rozdzielają cztery grupy:
- `wordpress_editor_metadata`: zmiany artykułów i liczba unikalnych stron;
  to **nie** jest trening wag.
- `knowledge.ingest` / `storage_readback_verified=true`: rzeczywiście
  przyjęte do pamięci źródłowej rekordy; to nie aktualizuje wag modelu.
- `training_performed=true` oraz `checkpoint_readback_verified=true`:
  zweryfikowany trening i liczbę `steps_this_job` można podać dopiero po
  otrzymaniu takiego poświadczenia. `weights_modified` nie dowodzi jakości.
- `independent_evaluation_verified=true` i
  `quality_improvement_proven=true`: dopiero taki dowód pozwala
  raportować zweryfikowaną poprawę jakości.

Wstępny odczyt z 11 listopada: 40 zwróconych metadanych edycji,
6 unikalnych postów w tej ograniczonej próbie. Log Render z 6 listopada
potwierdził 1 partię przeglądu, 10 rekordów źródłowych i
`model_weight_training=false`. Nie ma jeszcze pełnego
licznika rzeczywistych treningów modelu; raport `NOT_MEASURED`
jest poprawny.

**Bramki**: Hermes STOP/HOLD, B05/B06/B08 i 600 MiB — bez zmian.
Bez zatwierdzonej autoryzacji, źródła, podpisanego ACK i niezależnych
testów nie wdrażać żadnych zmian ani płatnych wywołań.

## Przykładowe powiadomienie po skutecznym źródle

MSW AI | 11.11.2026, 20:00
- Edycje: X w ostatniej zbadanej próbie (nie trening)
- Wiedza: Y potwierdzonych rekordów / przyrost od poprzedniego pomiaru
- Trening wag: Z potwierdzonych kroków (albo NOT_MEASURED)
- Alibaba: 32% miesięcznej puli (tylko jeśli źródło VERIFED_USAGE), reset: data
- Hermes: STOP/HOLD (bez zgadywania stopnia ukończenia)
- Zmiany: alert tylko jeśli nowe i zweryfikowane
