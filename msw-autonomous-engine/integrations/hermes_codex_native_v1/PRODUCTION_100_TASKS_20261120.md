# MSW Hermes ↔ Codex ↔ ChatGPT — 100 zadań

> Lista kontrolna. [x] tylko dla prac potwierdzonych dowodem w bieżącej sesji; pozostałe [ ] nie są deklaracją wykonania. Nie uruchamiać zadania 498623 ponownie bez niezależnego dowodu stanu efektu.

> Raportowanie: informuj po każdych 10 potwierdzonych fizycznych zmianach. Audyt i dokumentacja nie liczą się jako wdrożenia. Poprzednie 9/100 to wyniki audytu, nie 9 zmian produkcyjnych. Wymagany dowód: commit, test, wdrożenie i readback stosownie do zadania.

## Audyt istniejącego środowiska
- [x] 001. Potwierdź workspace Robot
- [x] 002. Zidentyfikuj główny worker Render
- [x] 003. Zidentyfikuj worker standby
- [x] 004. Zapisz aktywną gałąź produkcyjną
- [x] 005. Zapisz aktywny commit wdrożenia
- [x] 006. Sprawdź aktualny startCommand
- [x] 007. Sprawdź politykę autoDeploy
- [ ] 008. Sprawdź stan dysku /var/data
- [x] 009. Sprawdź aktualne błędy ACK
- [x] 010. Sprawdź status zadania 498623

## Inwentaryzacja kodu
- [ ] 011. Zidentyfikuj native_entry.py
- [ ] 012. Zidentyfikuj canary_admission_guard.py
- [ ] 013. Zidentyfikuj native_apply_bridge.py
- [ ] 014. Zidentyfikuj sqlite_apply_adapter.py
- [ ] 015. Zidentyfikuj hermes_chat_bridge.py
- [ ] 016. Zidentyfikuj github_chat_queue.py
- [ ] 017. Zidentyfikuj testy SQLite
- [ ] 018. Zidentyfikuj testy Codex
- [ ] 019. Sprawdź historię workflow Actions
- [ ] 020. Ustal jedyne źródło prawdy o zadaniach

## Kontrakt zadania
- [ ] 021. Zdefiniuj root_id
- [ ] 022. Zdefiniuj task_id
- [ ] 023. Zdefiniuj request_id
- [ ] 024. Zdefiniuj work_key
- [ ] 025. Zdefiniuj parent_id
- [ ] 026. Zdefiniuj statusy kolejki
- [ ] 027. Zdefiniuj zasady lease
- [ ] 028. Zdefiniuj format receipt
- [ ] 029. Zdefiniuj format ACK
- [ ] 030. Zdefiniuj wymagania effect readback

## Bezpieczeństwo zapisu
- [ ] 031. Sprawdź unikalność work_key
- [ ] 032. Sprawdź atomowy claim
- [ ] 033. Sprawdź czas ważności lease
- [ ] 034. Sprawdź zgodność lease token
- [ ] 035. Sprawdź idempotentny report
- [ ] 036. Sprawdź konflikt różnych wyników
- [ ] 037. Sprawdź SHA-256 treści
- [ ] 038. Sprawdź ACK po transakcji
- [ ] 039. Sprawdź odczyt po ACK
- [ ] 040. Sprawdź zachowanie przy niepewnym zapisie

## Uruchomienie Codexa
- [ ] 041. Zidentyfikuj obsługiwany tryb autoryzacji
- [ ] 042. Sprawdź dostępność Codex na Render
- [ ] 043. Zweryfikuj oddzielenie Pro od API
- [ ] 044. Sprawdź dostępne środowisko CLI
- [ ] 045. Ustal dopuszczalne narzędzia wykonawcy
- [ ] 046. Zbuduj bezpieczne wywołanie wykonawcy
- [ ] 047. Ogranicz czas pojedynczego wywołania
- [ ] 048. Ogranicz koszt pojedynczego wywołania
- [ ] 049. Zapisz receipt wykonawcy
- [ ] 050. Sprawdź SHA-256 receipt

## Połączenie adaptera
- [ ] 051. Powiąż receipt z oryginalnym task_id
- [ ] 052. Powiąż receipt z work_key
- [ ] 053. Zweryfikuj lease przed zapisem
- [ ] 054. Wywołaj istniejący report
- [ ] 055. Wywołaj istniejący ack
- [ ] 056. Zweryfikuj niezależny readback
- [ ] 057. Zablokuj drugi zapis po konflikcie
- [ ] 058. Zablokuj drugi dispatch po timeout
- [ ] 059. Zapisz diagnostykę bez sekretów
- [ ] 060. Zachowaj istniejącą kolejkę

## Testy regresyjne
- [ ] 061. Test poprawnego claim
- [ ] 062. Test wygasłego lease
- [ ] 063. Test błędnego tokenu
- [ ] 064. Test podwójnego claim
- [ ] 065. Test powtórzonego report
- [ ] 066. Test konfliktu digest
- [ ] 067. Test błędnego ACK
- [ ] 068. Test timeout po zapisie
- [ ] 069. Test readback mismatch
- [ ] 070. Test restartu procesu

## Testy integracyjne
- [ ] 071. Uruchom testy jednostkowe
- [ ] 072. Uruchom testy adaptera
- [ ] 073. Uruchom testy kolejki
- [ ] 074. Uruchom testy bezpieczeństwa JSON
- [ ] 075. Uruchom testy współbieżności
- [ ] 076. Uruchom testy CI GitHub Actions
- [ ] 077. Sprawdź logi nieudanych testów
- [ ] 078. Napraw błędy regresji
- [ ] 079. Uruchom ponownie testy po poprawkach
- [ ] 080. Zapisz raport testów z SHA

## Wdrożenie kontrolowane
- [ ] 081. Przygotuj commit release
- [ ] 082. Porównaj release z produkcją
- [ ] 083. Zweryfikuj backup konfiguracji
- [ ] 084. Zweryfikuj procedurę rollback
- [ ] 085. Uruchom bezpieczny canary
- [ ] 086. Sprawdź jednorazowy dispatch
- [ ] 087. Sprawdź natywny ACK canary
- [ ] 088. Sprawdź efekt po restarcie
- [ ] 089. Sprawdź brak duplikatów
- [ ] 090. Zatwierdź bramę produkcyjną

## Autonomia i odbiór
- [ ] 091. Uruchom worker bez podwójnego lidera
- [ ] 092. Ustaw kolejny task po ACK
- [ ] 093. Ustaw kontrolę stanu co 60 sekund na serwerze
- [ ] 094. Ustaw bezpieczny fallback wykonawcy
- [ ] 095. Ustaw STOP/HOLD dla niepewnych efektów
- [ ] 096. Ustaw alarm krytycznej awarii
- [ ] 097. Zweryfikuj zachowanie po restarcie Render
- [ ] 098. Zweryfikuj limit kosztów i kwot
- [ ] 099. Zweryfikuj pełny E2E Hermes–Codex–ACK
- [ ] 100. Zapisz końcowy raport produkcyjny

## Dowody audytu — 2026-11-20
- 001–007: Render workspace Robot `tea-dbeakqnavr4c73a9c8lg`; główny worker `srv-dbltdb9srm7s73ec0v00`, standby `srv-dbnodkbrjlhs739drc5g`; główny branch `msw/worker-autostart-final-20261111`, autoDeploy=no, startCommand rozpoczyna się od `python -B -` (embedded supplement transport V3).
- 005: ostatni potwierdzony live deploy `dep-dbuvtc7f3r2c73ekdpqg`, commit `105ad24c1c00ffdcad2857035419afaffa97dcdb`.
- 009–010: log Render 2026-11-20 15:43 UTC: `chat_blocked_no_ack` dla `msw-agent-v1:business:498623` (held), oraz `knowledge_effect_contract_required` (deferred_unverified). Stan po tej chwili niepotwierdzony.
- 008: rozmiar dysku 10 GB i mount /var/data są znane z konfiguracji, ale aktualne wykorzystanie dysku nie zostało odczytane — pozostaje otwarte.

## Licznik wdrożonych zmian
- Potwierdzone zmiany na produkcyjnym Renderze: 0/100.
- Dotychczasowe 9 odznaczeń dotyczy wyłącznie audytu.
- Raportuj kamienie milowe co 10 fizycznych zmian, nie co 10 kontroli.
