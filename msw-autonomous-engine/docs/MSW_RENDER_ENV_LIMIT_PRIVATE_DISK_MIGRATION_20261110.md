# MSW Render — prywatne pakiety poza zmiennymi środowiskowymi

## Potwierdzenie dostawcy i stan produkcji
- Support Render podał źródło wcześniejszych nieudanych wdrożeń: łączny rozmiar env > 1 MB. Wcześniejsze próby zatrzymywały się przed logami budowania.
- 2026-11-10T18:16:41Z nowy deploy `dep-dbplu9dg1s2s73dtjjng` dla `srv-dbltdb9srm7s73ec0v00` osiągnął `live`, commit `26f2e20d8b47`.
- Powiodły się proces build i start. Runtime pack (7 modułów) i własny model pokazały `installed_verified`; podpisane ACK nadal występują.
- Pamięć `MSW_BUSINESS_KNOWLEDGE` na ostatnim starcie pokazała `not_configured`. Oddzielny owner-context ma 63 potwierdzone rekordy. Nie udajemy, że biznesowy pakiet został przywrócony.
- Dysk istniejącej usługi: `/var/data` / 10 GB; katalog danych konfigurowany przez `MSW_DATA_DIR` (obecny plan `/var/data/msw-engine`). Żadna nowa usługa nie jest potrzebna.

## Dwa etapy, bez sekretów w GitHub
1. **Zachowanie źródła**: nie usuwaj aktualnych chunks ani małych pinów SHA podczas pierwszego wdrożenia nowego kodu. Kod instaluje pakiety jak dotąd i odkłada dokładne ZIP-y w prywatnym `MSW_DATA_DIR/private-pack-archives/<sha256>.zip` (0700 katalog, 0600 plik, zapis atomowy bez nadpisania).
2. **Dowód zapisu**: przy starcie Render wymagaj jednocześnie `runtime_pack=installed_verified`, `source=environment_migrated_to_disk`, `archive_readback_verified=true`, a dla modelu `own_model.state=installed_verified`, `source=environment_migrated_to_disk`, `archive_readback_verified=true`. W przeciwnym razie NIE USUWAJ env chunks.
3. **Niezależna kopia**: przed usuwaniem jedynej pozostałej kopii źródła upewnij się, że istnieje odtwarzalny, zabezpieczony backup lub autoryzowane prywatne object storage. Sam dysk Render nie zastępuje niezależnego backupu.
4. **Zmniejszenie środowiska**: dopiero po potwierdzeniu kopii usuń konkretne istniejące wartości `MSW_RUNTIME_PACK_00...`, `MSW_OWN_MODEL_PACK_00...` i (o ile istnieją) `MSW_BUSINESS_KNOWLEDGE_00...`. Ustaw odpowiednie `*_CHUNKS=0`. ZACHOWAJ krótkie `*_SHA256` jako piny. NIE zmieniaj `MSW_SECRET`, `MSW_NODE`, `MSW_WP_URL`, `MSW_TRANSPORT_URL` ani innych kluczy dostępowych.
5. **Start wyłącznie z dysku**: nowe wdrożenie ma pokazać `source=verified_private_disk` z `archive_readback_verified=true` dla runtime i modelu. Zweryfikuj kilka faktycznych `module.execute` oraz `llm.generate` z `server_ack_verified=true` i sprawdź stan COMBO.
6. **Rollback**: jeśli odczyt dysku nie przechodzi, nie zastępuj ich nieznanym archiwum i nie ponawiaj zadań; przywróć wyłącznie dokładne poprzednie env chunks z niezależnego backupu, zachowując SHA i node identity. Przerwij rollout po pierwszym nieudanym readback.

## Zasady implementacji
- Dane pakietów nie trafiają do repozytorium, logów, endpointów REST ani wiadomości ChatGPT. GitHub zawiera tylko kod i dane syntetyczne w testach.
- Nazwa archiwum wynika wyłącznie z 64 znaków SHA-256; brak dowolnych ścieżek, symlinków i podmiany pliku. Archiwum ZIP jest ponownie hashowane i przechodzi dotychczasową weryfikację manifestu.
- Jeśli opcjonalny zapis archiwum na dysku się nie uda, starszy prawidłowy pakiet z env nadal może działać, ale diagnostyka pokazuje `archive_readback_verified=false`. Taki wynik blokuje usuwanie env.
- Jeśli przy starcie nie ma ani env chunks, ani archiwum przypiętego SHA-256, pakiet zostaje zablokowany; nie pobiera kodu z Internetu i nie podmienia prywatnego modelu.
- `MSW_BUSINESS_KNOWLEDGE` odtwarza tylko wcześniej zweryfikowany JSON z dysku, jeżeli znane jest dokładne SHA. Gdy brak pinu, zwraca `not_configured` jak poprzednio.

## Testy
- GitHub Actions: https://github.com/flyxpressWarszawa/KonyWarsaw/actions/runs/42876132017
- 12 nowych syntetycznych testów migracji: PASS.
- 11 dotychczasowych testów runtime_pack: PASS.
- 2 dostępne testy izolacyjne own_model: PASS; 3 testy prywatnego checkpointu pominięte (ich dane nie były wyprowadzane poza prywatne środowisko).

## Ograniczenie narzędzi operacyjnych
Obecny konektor Render pozwala bezpiecznie MERGE/REPLACE env, lecz nie udostępnia odczytu pełnej listy nazw i rozmiarów zmiennych ani osobnej operacji DELETE. Nie wykonywać `replace=true`, gdy nie można skopiować wszystkich małych sekretów. Nie czyścić na ślepo wszystkich 32+32+20 możliwych kluczy, bo tworzyłoby to niepotrzebne puste zmienne. Eksport i usunięcie tylko rzeczywiście istniejących chunks należy przeprowadzić kontrolowaną drogą dostępną w Render, po potwierdzonym backupie.

Status na etapie PR: kod i testy przygotowane; nie scalono, nie usunięto env i nie wykonano tego nowego etapu migracji na produkcji.