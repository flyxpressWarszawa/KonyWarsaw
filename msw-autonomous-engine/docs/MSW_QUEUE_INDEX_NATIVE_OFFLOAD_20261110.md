# MSW – indeks kolejki i wydzielenie obliczeń (kandydat do wdrożenia)

**Data audytu:** 10.11.2026. **Status: STAGED / NO-GO na produkcji.**

## Potwierdzone fakty
- Centrala: `mobilny-serwis-warszawa.pl`, tabela `ovse_mswue_remote_jobs`, MariaDB `10.11.19-MariaDB-cll-lve`, silnik InnoDB.
- Brak indeksu `(state,kind,created_at,id)`; istnieją m.in. `pending(state,created_at)` i `results(state,applied)`.
- EXPLAIN dla pustej kolejki `seo.analyze` szacował ok. 8 268 badanych wierszy. Jest to estymacja optymalizatora, a nie pomiar fizycznych odczytów.
- Pojemność danych tabeli historycznie ok. 142 MB, ~16–20 tys. wierszy. Liczby dynamiczne.
- W momencie kontroli: 12 063 `module.execute`, 359 `engine.v3.ingest`, 213 `runtime.catalog`, 114 `supreme.observe` oczekujących. Liczby z różnych momentów.
- **Brak zweryfikowanej kopii odtwarzalnej** i testu przywrócenia (`backup_verified=false`, `restore_test_verified=false`).
- Ostatnia wartość CPU konta wynosiła 86% i nie były dostępne dwa nowe niezależne odczyty poniżej 60%; odczyt liczby ticków PHP nie jest tym samym, co obciążenie konta.
- Główny worker Render raportował regularnie `server_rejected_request` HTTP 404 dla swojego transportu. Dokładny skonfigurowany URL wymaga weryfikacji bez ujawniania sekretów. Inne linie Render działają – nie wolno uogólniać awarii na całą flotę.
- Implementacja `module.execute` na Render wykonuje `gateway('autonomy_execute')`, który deleguje natywne callbacki **z powrotem do WordPressa**. Dodanie workerów z tym samym adapterem nie przenosi obciążenia PHP.

## Zaproponowany indeks – nie uruchamiać bez przejścia bramek
```sql
ALTER TABLE ovse_mswue_remote_jobs WAIT 3
  ADD INDEX IF NOT EXISTS mswue_state_kind_created_id (state,kind,created_at,id),
  ALGORITHM=NOCOPY, LOCK=NONE;
```
MariaDB wspiera dla zwykłego indeksu InnoDB `ALGORITHM=NOCOPY` i `LOCK=NONE` w tej rodzinie wersji. Nadal występują krótkie blokady metadanych na początku/końcu, a tworzenie indeksu zużywa CPU oraz I/O. Zatem **nie jest to operacja bez ryzyka**.

## Bramki konieczne przed jakimkolwiek DDL
1. Zweryfikowany pełny backup bazy (nie samo istnienie plików) wraz z udanym testem odtworzenia.
2. Dwa odrębne, świeże odczyty CPU i RAM konta poniżej 60%, rozdzielone co najmniej 45 s; monitorowanie przy zmianie.
3. Potwierdzenie uprawnień ALTER, zapasu dyskowego, braku długich transakcji i oczekujących blokad metadanych.
4. Zgodność nazwy, układu tabeli i brak konfliktującego indeksu.
5. Okno serwisowe, wyraźnie zatwierdzona zmiana, monitorowanie `EXPLAIN`, error rate, CPU, ACK przed/po.
6. Jeśli indeks istnieje, STOP – tylko kontrola planów zapytań. W przypadku błędu STOP bez automatycznego ponowienia.

Rollback po odrębnej ocenie (też DDL z ryzykiem):
```sql
ALTER TABLE ovse_mswue_remote_jobs WAIT 3
  DROP INDEX IF EXISTS mswue_state_kind_created_id,
  ALGORITHM=NOCOPY, LOCK=NONE;
```

## Co da, a czego nie da indeks
Pomaga filtrować po stanie i typie zadania, szczególnie selektywne `seo.analyze`. Zapytania z `ORDER BY CASE ...` mogą nadal wymagać sortowania. Nie rozładowuje sam z siebie zaległych 12 tys. `module.execute`, ani nie rozdziela obliczeń PHP pomiędzy hostingi. Wymaga EXPLAIN przed/po oraz pomiaru kosztu żądań.

## Wydzielenie obliczeń `module.execute`
`module.execute` aktualnie przenosi żądanie sterujące, **nie CPU WordPressa**. Podział należy zrobić wewnątrz autoryzowanych wykonawców natywnych, z utrzymaniem `execution_id`, `site_id`, przypisania właściciela, lease, CAS, HMAC i read-back ACK. Na inne hostingi można przenosić wyłącznie jawnie rozpoznane zadania deterministyczne, które nie używają WP DB, nie edytują wpisów, nie wymagają magazynu prywatnego ani zgód klienta. Nie wolno generować drugiego wykonania tego samego `execution_id`.

Dalsze typy `engine.v3.ingest` i `supreme.observe` mają istniejące prywatne implementacje Python z trwałym magazynem; nie są odpowiednie do prostego skopiowania do hostingu SEO. `runtime.catalog` jest najpierw do zweryfikowania na istniejącym Render. Wysokie opóźnienia Render 404 trzeba usunąć przed zwiększaniem liczby zgłoszeń, żeby nie przeciążać centrali.

## Audyt i testy
`python msw_queue_index_preflight.py snapshot_20261110.json`

Skrypt **nie ma możliwości łączenia się z DB, uruchamiania SQL ani wykonywania HTTP**. Domyślnie zawsze `write_permitted=false`. Sprawdza jedynie dostarczone, zanonimizowane metadane.

`python -m unittest discover -p "test_msw_queue_index_preflight.py" -v` – 17 testów obejmujących zabezpieczenia oraz klasyfikację natywnych zadań.

### Źródła techniczne
- https://mariadb.com/docs/server/reference/sql-statements/data-definition/alter/alter-table
- https://mariadb.com/docs/server/server-usage/storage-engines/innodb/innodb-online-ddl/innodb-online-ddl-overview
- https://mariadb.com/docs/server/server-usage/storage-engines/innodb/innodb-online-ddl/innodb-online-ddl-operations-with-the-nocopy-alter-algorithm

**W żadnym kroku tworzenia tego pakietu nie wykonano mutacji produkcyjnej bazy, nie aktywowano natywnych callbacków ani nie zmieniono kolejki.**

## Dodatkowy audyt źródeł natywnych — 2026-11-10
- Centralny `MSW_Module_Autonomy` 3.1.1 ma 219 delegowanych grup callbacków i 301 zarejestrowanych modułów w bieżącym odczycie.
- Natywne `module.execute` nie zakończyły nowego zadania od 2026-11-08T00:00:18Z; backlog wzrósł do 12 223 pending (ostatni odczyt, dynamiczny).
- Odczyt `/msw-federation/v1/autonomy/manifest` zwrócił **257/262** zgodnych bieżących źródeł.
- Pięć plików ma inne SHA-256 niż zapisany hash po migracji: `msw-ai-direct-control/msw-ai-direct-control.php`, `msw-domain-agent/msw-domain-agent.php`, `msw-fotoatlas-portal/msw-fotoatlas-portal.php`, `msw-hunter-products/msw-hunter-products.php`, `msw-portal-combo/msw-portal-combo.php`.
- Pliki są nadal aktywne. Część różnic wiąże się z nowszą wersją wtyczki (np. COMBO 108.1.5, Domain Agent 1.1.0); część zachowała numer wersji mimo zmiany zawartości.
- Niezgodność manifestu nie jest sama w sobie dowodem zagrożenia ani nie oznacza, że każda operacja tych modułów jest blokowana. `trustedPin()` ma dodatkowe zweryfikowane wyjątki. Starsze zadania mogą jednak zwrócić `source_changed`, jeśli żaden jawny wyjątek nie pasuje.
- **Nie nadpisywać** historycznych hashy, nie akceptować automatycznie źródeł i nie ponawiać masowo `module.execute`. Najpierw podpisane porównanie pochodzenia każdej aktualizacji, jej zakresu i zatwierdzonej historii release.
- `MSW Unified Pressure Repair Once` ma już zweryfikowane potwierdzenie `file_readback_verified` dla obecnego `RemoteBridge.php`. Nie aktywować tej jednorazowej poprawki ponownie.
- Bieżący alarm centrali obejmował 100% CPU i 100% RAM; brak zweryfikowanego backupu i testu restore wciąż oznacza **NO-GO dla produkcyjnego ALTER TABLE**.

Plan: naprawić etap HTTP 404 z PR #15 na bazie zatwierdzonego, oznaczonego logu; potem oddzielnie zbadać pięć podpisów źródłowych i jedną kontrolowaną, autoryzowaną realizację. Nie wykonywać żadnych działań natywnych bez lease, CAS, HMAC i source-native ACK.
