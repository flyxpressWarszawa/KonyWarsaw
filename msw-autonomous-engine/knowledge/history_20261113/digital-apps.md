# MSW Digital Apps — od pierwszych publikacji QA do bramki sprzedażowej

**Zakres:** aplikacje cyfrowe rozwijane w ramach istniejącego Business Hunter/MSW LAB. Publikacja DEMO ≠ udostępnienie płatnego towaru.

## Identyfikatory i różnicowanie produktów

| Aplikacja | Woo product ID / SKU | Funkcja faktyczna lub docelowa | Stan sprzedaży |
|---|---|---|---|
| Energia Ultra / Energia z Eksportu Ultra | #61195 / `MSW-LAB-ENERGY-ULTRA-100` | Lokalny audyt CSV Home Assistant/Shelly: braki w osi czasu, duplikaty, resety liczników, anomalie, interpretacja jednostek | QA/draft, niepotwierdzona chroniona dostawa |
| KartaCzęści QA Ultra | #61206 / `MSW-LAB-PARTS-QA-100` | Walidacja SKU, producentów, modeli, jednostek, napięcia, mocy i niespójnych specyfikacji | QA/draft, niepotwierdzona chroniona dostawa |
| Woo Import Lab Ultra / Import WooCommerce QA Ultra | #61216 / `MSW-LAB-IMPORT-QA-100` | Porównanie dwóch eksportów CSV SKU/cena/stan, raport różnic i ostrzeżeń, **bez automatycznego importowania** do Woo | QA/draft, niepotwierdzona chroniona dostawa |

**Nie dublować:** starszy produkt Energia CSV ma ID #25059 i nie zastępuje funkcji diagnostycznych Energia Ultra.

## Miejsca publikacji

- Główna centrala: `https://mobilny-serwis-warszawa.pl/nowe-biznesy-ai/`, WP #61102 — publiczna informacja; `https://mobilny-serwis-warszawa.pl/msw-digital-apps/`, WP #61225 — publiczny portal informacyjny z etykietami QA.
- Satelita `mswdigitalapps.pl`: homepage #9; w checkpointcie 5.11 strony demonstracyjne #36–44; 6.11 aplikacje QA #149 Energia, #148 KartaCzęści, #150 Woo Import, Fabryka #76 i informacyjny katalog/sklep #153. 19 demonstracji w historycznym odczycie. Nie było tam dowodu działającego WooCommerce / zakupów.
- Strony i produkty mają różnych właścicieli publikacji. Nie kopiować opisu, ceny, grafiki ani pliku między domenami bez praw, dopasowania, unikalności i QA.

## Zrealizowany etap offline

Zestaw v1.0.0 utworzono lokalnie i historycznie zgłoszono 15 zaliczonych testów. Kolejne iteracje dotyczyły parserów CSV, cytowań i separatorów, lokalnego bezpieczeństwa importu, znaków Unicode/CSV formula injection, diagnostyki energii oraz raportów QA.

**Audyt 13.11:** pakiet dowodowy `MSW-HUNTER-QA-CHROMIUM-WOO-SCHEMA-20261113.zip` — SHA-256 `46db6a93cba3926c645811c5095c3fae50eaac0fd260025cde4ae83949f4bc97` (to hash PACZKI DOWODOWEJ, nie źródłowego ZIP aplikacji). W lokalnym, izolowanym środowisku: 122/122 regresje Node.js PASS, 11/11 kontrole Chromium PASS. Sprawdzono m.in. fikcyjne CSV, eksport raportu, blokadę niebezpiecznych SKU i niewykonanie HTML. Brak stwierdzonych zewnętrznych żądań w teście. NIE jest to niezależny test bezpieczeństwa serwera, prawdziwe Safari/iPhone, checkout ani płatność.

**Nowa luka kompatybilności:** mimo poprawnego prostego `UPDATE`, Woo Import Lab R7 traktuje jako błąd lub niepełność niektóre legalne warianty eksportu Woo: ujemny stan przy backorders, `Stock=parent`, pusty stan przy wyłączonym śledzeniu magazynu, puste SKU z podanym ID. To ograniczenie funkcjonalności, a nie argument do automatycznego wykonania importu. Rozwiązanie musi rozróżnić `REVIEW` od twardego błędu, z testami na legalnie otrzymanych danych.

Konkurencja i źródła do fakt-checkingu: oficjalny importer/exporter WooCommerce `https://woocommerce.com/document/product-csv-importer-exporter/` oraz źródła publiczne do walidacji rynku. Bez Google SERP PL i realnych zgłoszeń klientów nie deklarować nowej szansy ani biznesowego ROI.

## Release gate — NO-GO (13.11)

Lokalny `gate_result.json` zwrócił `release_decision=NO-GO`, `central_ack=false`. Trzy produkty w tym odczycie nie miały potwierdzonych plików do pobrania, ceny, publikacji i `purchasable`; te pola pozostają **stanem odczytu z konkretnego raportu**, nie niezmiennym twierdzeniem na przyszłość.

Przed uruchomieniem sprzedaży muszą istnieć wszystkie niezależne dowody:

1. Świeży pełny backup plików **i** bazy z możliwością odtworzenia, niezależny czysty skan bezpieczeństwa.
2. Legalne dane testowe z realnej instalacji, testy zgodności i uprawnień, kontrolowane canary, rollback.
3. Serwerowa obecność właściwego ZIP, jego `sha256` odczytane na miejscu oraz poprawna obsługa chronionego pobrania.
4. Weryfikacja opłaconego zamówienia Woo, praw nabywcy i rzeczywistej dostawy pliku, bez niepewnych obciążeń.
5. Trwały Native Factory/COMBO receipt + niezależny ACK i readback; dopiero wtedy karta produkcyjna.

Nie wolno obejść bramek przez ręczne ustawienie `purchasable` ani podmianę statusu w raporcie. Nie zmieniać cen, VAT, PayPal, SKU, budżetów, CRON ani domen. Stan QA może być publicznie opisany jako QA, nigdy jako już sprzedawany program.

## Następna wykonalna praca

P1: rozliczyć macierzysty receipt Native Factory #586, bez ponowienia niepewnego POST; P2: przeprowadzić rozszerzone testy interpretacji wariantów stock/backorder na posiadanych legalnych eksportach; sprawdzić SHA oryginalnego archiwum i aktualne dane produktów na właściwym WordPress. P3: dopiero potem 7-dniowa ocena popytu na cyfrową usługę audytu CSV (3–5 firm, osobno liczba problemów, fałszywych alarmów, zapytań i legalnych leadów). Pomiary sprzedaży, przychodu, ROI i kosztów, których nie odczytano: `NULL`.
