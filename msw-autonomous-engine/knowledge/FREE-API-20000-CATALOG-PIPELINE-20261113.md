# MSW Hermes — katalog 20 000 API (etap discovery, bez produkcji)
Data: 2026-11-13. Cel: **20 000 unikalnych, naprawdę zweryfikowanych darmowych API**. Nie przedstawiać dokumentacji ani URL jako działających API.

## Co działa
`tools/free_api_catalog.py` — offline importer JSON / JSONL / CSV; normalizacja URL, SHA-256 ID, deduplikacja, odrzucanie prywatnych adresów i URL z sekretami, wynik JSONL i raport. Każdy rekord początkowo ma `zero_cost_verified=false`, `live_http_verified=false`, `enabled=false`. Żaden endpoint nie jest wywoływany.
Przykład: `python msw-autonomous-engine/tools/free_api_catalog.py --input sources.json --output-dir /tmp/msw-catalog`.

## Kolejne źródła do osobnego sprawdzenia
- public-apis/public-apis (lista społecznościowa, wpisy wymagają ponownej weryfikacji)
- APIs.guru/openapi-directory (specyfikacje OpenAPI; nie wszystkie darmowe)
- OpenAPI Directory, API Commons, data.gov, data.europa.eu, Wikidata, Eurostat, World Bank (katalogi/dokumentacje, nie automatycznie API do modelu AI)
- oficjalne cenniki i strony auth poszczególnych dostawców (źródła prawdy dla darmowego limitu)

## Bramy walidacji
1. Zachować URL oryginalnej dokumentacji i datę odczytu.
2. Odseparować stronę dokumentacji od właściwego endpointu.
3. Zweryfikować bieżący brak opłat i zasady użycia **na koncie MSW**, bez obchodzenia limitów.
4. Przetestować bezpieczne żądanie bez danych klientów, rejestrować HTTP, limit i odpowiedź.
5. Dopuścić wyłącznie po zatwierdzeniu prywatności i trwałym, podpisanym receipt Hermesa.
6. Unikać automatycznej aktywacji, masowych pingów i dodatkowego crona.

## Rozdzielne liczniki
`source_documents` ≠ `discovered_unique_urls` ≠ `candidate_endpoints` ≠ `verified_free_api` ≠ `hermes_connected`.
Istniejące partie 008–014 nie dowodzą 700 różnych API. Zliczać dopiero po scaleniu i odfiltrowaniu duplikatów.
