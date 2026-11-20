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
- [x] 076. Uruchom testy CI GitHub Actions
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

## Dowód testów — 2026-11-20
- 076: GitHub Actions Hermes Codex admission run 44772601726: SUCCESS; Hermes Chat Loop 44772614294: SUCCESS; WordPress registry 44772614308: SUCCESS, commit 6defa75858aac5f03ff031a6545f367f0e0d4501.
- Render live deploy dep-dbuvtc7f3r2c73ekdpqg nadal commit 105ad24c1c00ffdcad2857035419afaffa97dcdb. CI nie jest testem E2E z Codexem na Renderze; zadania 95 i 99 pozostają otwarte.

## Render readback 2026-11-20 16:43 UTC
- Standby deploy dep-dc07grrncjis73808csg LIVE on 60bb7361f73f; process started at 16:43:00 UTC.
- Runtime log: public_head_matches_tested=false; overlay pinned to a4dea48638e7721fa3e318de1eb4b2c2c4029908. No production Codex execution can be claimed.
- Staging probe: github_queue_connected=true, live_external_chat_connected=false; ACK counts 1/1 are storage-consistency only, not native Hermes identity.
- Blocker: runtime overlay compatibility pin and Codex auth/real E2E. Do not re-dispatch blocked task 498623.

## Render production deployment evidence — 2026-11-20 16:48 UTC
- Standby deploy dep-dc07j749v7es73as12t0 LIVE; commit 8023610c8e2fd16d563e504492f24237d42fba44; finished 16:48:06Z.
- Runtime process is still /tmp/msw-aux-seo/hermes-chat-staging/overlay-v7-repinned-a4dea/overlay_launcher.py.
- Runtime public_head_matches_tested=false (tested_head a4dea48638e7721fa3e318de1eb4b2c2c4029908); live_external_chat_connected=false. Native Codex E2E and ACK remain unverified.
- Release gate: DO NOT count this as successful Codex integration. Change actual Render startCommand/overlay pin only with compatible, verified runtime; do not redeploy same config as a workaround.

## Izolowany test Codex → SQLite → natywny ACK — 2026-11-20
- Na głównym workerze Render, w osobnym katalogu `/var/data/msw-hermes/codex-sqlite-canary-20261120`, uruchomiono rzeczywistą inferencję przez istniejącą prywatną sesję Codex App Server (`codex login status`: `Logged in using ChatGPT`). Wynik tekstowy `MSW_CODEX_SQLITE_CANARY_OK` uzyskano bez iteracji narzędzi.
- Syntetyczne zadanie `msw-canary-codex-20261120-01`, `work_key=msw-canary-work-20261120-01`, użyło istniejącego SQLite `hermes_chat_bridge` i `sqlite_apply_adapter`; report i natywny ACK zakończyły się stanem `acknowledged`. SHA-256 wyniku: `fa5ee8e9fba153a4208ed8022ef1348cb59b117912adcb8bfa0c6d0deb64104c`; SHA-256 receipt: `1a258622a5b725d07f9fceab5053a2e8381d458b7f89b6755bc9f0460bdae652`.
- Niezależny proces odczytał trwałą bazę i receipt: stan `acknowledged`, oba digesty zgodne, dokładnie jeden rekord z tym `work_key`. Kod czterech modułów do testu był porównany SHA-256 z publiczną gałęzią przy HEAD `45c75b366b1314a18bf8406d717f241baa2400af`.
- Granica dowodu: to test izolowanego zadania SQLite na dysku głównego workera, bez ingerencji w produkcyjną kolejkę GitHub. Nie potwierdza dispatchu przez standby, autoryzacji Codex na standby, restartu, efektu biznesowego ani automatycznej pętli Hermes–ChatGPT–Codex. Produkcyjne wdrożenie funkcjonalne pozostaje **0/100**. Zadanie `msw-agent-v1:business:498623` nie zostało ponowione; w kolejce GitHub ostatni odczyt pozostawił rewizję 100 i stan `reported/blocked` bez własnego ACK.
- Standby po wdrożeniu `dep-dc07r7l6e07s73bu0f8g` uruchamia repozytoryjne `standby_safe_entrypoint.py`, ale nadal wykonuje starszy overlay przypięty do `a4dea48638e7721fa3e318de1eb4b2c2c4029908`; `public_head_matches_tested=false`, `MSW_CODEX_PRODUCTION_DISPATCH=0`, Codex CLI 0.170.0 jest dostępny, a `CODEX_HOME` standby ma status `Not logged in`. Stary overlay i transport V3 zachowano.

## Kontrola relacji zadania i stan usług — 2026-11-20, kolejny checkpoint
- Commity `0a0bf53b37e333278dd88593d09c255b524230a7` i `11f1a92e307efb268d7c42fd43903f07ef26ff26`: adapter SQLite porównuje `parent_id` z rekordem przed `report` i po `ack`; testy tworzą rzeczywiście potwierdzonego rodzica i dowodzą, że obcy `parent_id` zatrzymuje zapis. Lokalnie 6/6 testów adaptera i 23/23 testy istniejącego mostu przeszły. Odczyt pliku z gałęzi po commicie potwierdził zapis. CI dla tych commitów oraz wdrożenie na Render nie zostały tu potwierdzone; jest to poprawka kodu, nie zaliczona zmiana produkcyjna.
- Standby: 10 publicznych i 5 modułów overlay przechodzą kontrolę dokładnych SHA-256 w `--verify-only`. `public_head_matches_tested=false` dotyczy HEAD repozytorium względem przypiętego `a4dea...`; odrębny warunek zgodności wszystkich wskazanych plików przechodzi. Uruchomiony overlay nadal zawiera starsze wersje pięciu modułów, więc brama nowej trasy pozostaje zamknięta.
- Niezależny odczyt prywatnej kolejki GitHub po testach: rewizja 100, SHA bloba `364f5ac0e5e81093a2a41625af442479542a4bb7`, zadanie `1a6648ce-cc38-5ee3-a365-c1b358774b2b` (`498623`) nadal `reported/blocked` bez własnego `hermes_ack`. Nowych zadań ani drugiego wykonania nie utworzono.
- Główny worker emituje nadal `deferred_unverified` z przyczyną `knowledge_effect_contract_required`; standby nie ma autoryzowanej sesji CLI Codex. Istniejąca godzinowa automatyzacja `Automation_03623231db8481918103b09295fc67c9` jest `is_enabled=false` (odczyt własnego peek); nie utworzono konkurencyjnej automatyzacji. Produkcyjny E2E i licznik 0/100 pozostają bez zmiany.

## Odzyskanie niepewnego report — 2026-11-20
- Commity `c0460e16724c044d3ac748e6473e168373f9f46c` i `8995a1a74bd313e47412200250b731ad962c903e`: w istniejącym adapterze SQLite odczyt potwierdza zgodność lease, tożsamości, tekstu, digestu i outcome przed dalszą operacją. Jeśli `report` został już zapisany, adapter nie wysyła go ponownie; przy identycznym wyniku wystawia przez istniejący most ACK i odczytuje skutek, a przy konflikcie pozostawia zadanie bez ACK. Test symuluje utratę odpowiedzi po zapisie i wygasły lease; blokuje wywołanie drugiego `report`.
- Lokalnie 8/8 testów adaptera, 23/23 testy mostu oraz 2/2 testy startu standby PASS. Odczyt z GitHub potwierdził kod bloba `3be1b965822354ff97016d849ad6c8af2cc147c6`. Dwa przebiegi powiązane z commitem kodu: Hermes WordPress registry staging #44793486070 SUCCESS, MSW Hermes Chat Loop offline #44793486225 SUCCESS. Nie potwierdzono tutaj wyniku odrębnego workflow Hermes Codex admission dla tego commita.
- Kolejka GitHub nadal rewizja 100, SHA bloba `364f5ac0e5e81093a2a41625af442479542a4bb7`; `498623` ma `reported/blocked` bez `hermes_ack`. Zmiana nie została wdrożona na standby (`autoDeploy=no`), nie zastępuje starego overlay ani nie potwierdza autoryzacji Codex na standby. Licznik wdrożeń funkcjonalnych pozostaje 0/100.
