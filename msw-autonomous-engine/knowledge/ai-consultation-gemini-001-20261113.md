# Konsultacja z istniejącym Gemini — publiczny temat bezpieczeństwa API

Data: 2026-11-13. Bez kluczy, danych klienta, prywatnego kontekstu MSW ani wewnętrznych logów.

- `gemini_status`: połączenie odpowiada.
- `gemini_models`: udostępnia listę modeli; nie jest to dowód uprawnienia do generowania na każdym modelu.
- Dwie próby `gemini_generate` z nazwami `gemini-2.5-flash-lite` i `gemini-2.5-flash`: zwróciły komunikat „Google nie udostępnia wskazanego modelu dla tego połączenia”, więc żaden tekst z tych dwóch prób nie został wygenerowany.
- Trzecie wywołanie `gemini_generate` bez jawnej nazwy modelu: zwróciło 3 techniczne reguły fallback. Dokładny model **nie został potwierdzony** w odpowiedzi.

## Ważna korekta bezpieczeństwa
Sugestia modelu, aby po abortowaniu socketu automatycznie przełączyć dostawcę, **została odrzucona**. Zerwanie połączenia/timeout/HTTP 503 nie stanowią dowodu, że poprzedni model nie wykonał płatnego żądania. Przy statusie niepewnym należy HOLD i sprawdzić potwierdzony receipt lub idempotentny status u dostawcy, zanim zadanie uruchomi się ponownie.

Elementy użyteczne, ale wymagające niezależnej weryfikacji:
- limitowanie żądań na dostawcę oraz respektowanie `Retry-After`;
- izolacja prywatnych danych MSW od anonimowych serwisów;
- nieużywanie wielu kont/kluczy tego samego dostawcy do obchodzenia darmowych limitów;
- fallback między niezależnymi zatwierdzonymi dostawcami tylko z dowodami zerowej ceny i uprawnień.

Ta konsultacja to **zewnętrzna nieufna opinia**, nie konfiguracja produkcyjna ani dowód dopuszczonego dostawcy do stałej pracy. `Model list` nie jest `inference ACK`.
