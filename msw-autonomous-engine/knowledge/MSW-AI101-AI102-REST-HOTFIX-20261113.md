# Potwierdzona minimalna naprawa WordPress REST — AI101 i AI102

Data 2026-11-13. Dotyczy tylko centrali mobilny-serwis-warszawa.pl, nie całej floty 41 domen.

## Objaw
- `GET /wp-json/msw-ai101/v1/status` — PHP fatal: `call_user_func` nie mógł wykonać `private MSWPC_AI101_Admin::allowed()` jako `permission_callback`.
- `GET /wp-json/msw-ai102/v1/status` — analogiczna metoda `private MSWPC_AI102_Admin::permitted()`.

## Zakres poprawki wykonanej na właściwym koncie GoDaddy
- `wp-content/plugins/msw-portal-combo/includes/class-ai101-admin.php`: zamieniono **wyłącznie** `private static function allowed():bool` na `public static function allowed():bool`.
- `wp-content/plugins/msw-portal-combo/includes/class-ai102-admin.php`: analogicznie `permitted()`.
- Same sprawdzenia `current_user_can('manage_options') && MSWPC_Core::is_hub()` pozostawiono bez zmian.

## Dowody
- Podpisany agent HMAC potwierdził `backup_created=true` przy obu zapisach; oba odczyty zwrotne SHA-256 są zgodne.
- AI101 po zmianie: `4361e606eea8daf7da675115454fb33c3f18e978c0031285a7aaec71122bae9b`.
- AI102 po zmianie: `a75e57cf76f6e2cd2713f64f52f501866b34c8601c9def7ab178b07505602ce4`.
- Oba uwierzytelnione `GET /status` odpowiadają obecnie poprawnym JSON bez fatal 500.
- Test CLI PHP lint na agencie HMAC jest **niedostępny**, a więc nie może być zaliczony jako dodatkowy wynik PASS. Poprawna odpowiedź REST dowodzi, że callback został użyty przez WordPress.

## Ograniczenia
- Brak nowych adapterów AI: AI101 pokazuje `registered=0`, `tested_24h=0`; AI102: `missing_providers=27`, `fully_reviewed=0`. Naprawa statusu nie uruchamia dostawców.
- Pełny backup hostingu z 11.11.2026 widoczny w metadanych cPanel; **odtworzenie pełnego backupu nie zostało sprawdzone**.
- Następna aktualizacja COMBO może nadpisać hotfix. Trzeba poprawić źródło bazowe pakietu i zaliczyć testy po aktualizacji; nie kopiować sekretów ani prywatnych plików do publicznego repozytorium.
