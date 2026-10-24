# MSW AI — niezależny portal technologiczny (msw-ai.com)

## Zakres zrealizowany w repozytorium
- Backend FastAPI, SQLAlchemy; bez WordPressa.
- Nowa aplikacja `app.platform_main:app`, strona `/` przy `MSW_PORTAL_PRIMARY=1`.
- Responsywny interfejs `app/static/portal.html`, `portal.css`, `portal.js`.
- Prywatne konta klientów: PBKDF2-SHA256 z indywidualną solą, sesje w bazie, HttpOnly Secure, CSRF.
- Kreator do 5 robotów / konto, 10 technologicznych dziedzin, role analityk / diagnosta / serwisant / integrator / badacz.
- Lokalny, wywoływalny silnik regułowy; opcjonalne wywołania OpenAI przy skonfigurowanym kluczu i jawnym zezwoleniu.
- Arena: zestaw 10 realnie wykonywanych testów klasyfikacji technicznej, bez fikcyjnej „mocy AI”.
- Giełda publicznych profili (wyłącznie zweryfikowanych autorów), prywatne zamówienia dedykowanych botów.
- Statystyki z bazy — brak fikcyjnych użytkowników, aktywności i wolumenu.

## Adresy
- Nowy portal: `https://msw-ai-com-portal.onrender.com/`
- Docelowo: `https://msw-ai.com/`
- Endpoint stanu: `/api/portal/status`
- Dziedziny: `/api/portal/domains`
- Katalog: `/api/portal/market`
- Arena: `/api/portal/arena/overview`
- Metryki: `/api/portal/metrics`

## WAŻNE: konta klientów pozostają bezpiecznie wyłączone, dopóki DATABASE_URL nie wskazuje trwałej bazy PostgreSQL
Darmowy Render Web Service może spać i nie gwarantuje trwałości danych na lokalnym dysku.
Nie włączaj `MSW_ALLOW_EPHEMERAL_ACCOUNTS=1` w produkcji.

1. W Render otwórz zasób `msw-ai-roboty-db` w tym samym workspace Frankfurt.
2. Skopiuj **Internal Database URL** do bezpiecznej zmiennej środowiskowej usługi `msw-ai-com-portal`: `DATABASE_URL`. Nie wpisuj URL bazy do publicznego GitHub ani czatu.
3. Wdróż usługę ponownie. Backend wykona `Base.metadata.create_all()`, zakładając tabele w istniejącej bazie (nie kasuje istniejących).
4. Otwórz `/api/portal/status`. Wymagane: `database=connected`, `durable=true`, `signup_ready=true`.
5. Koniecznie skonfiguruj SMTP dla potwierdzania e-mail przed udostępnieniem publicznej giełdy: `MSW_SMTP_HOST`, `MSW_SMTP_PORT`, `MSW_SMTP_USER`, `MSW_SMTP_PASSWORD`, `MSW_SMTP_FROM`.
6. Dodaj politykę prywatności, umowę powierzenia danych dostawcy hostingu, właściwy regulamin i kopie zapasowe przed promocją produkcyjną.

Istniejąca bezpłatna baza Render `msw-ai-roboty-db` ma termin wygaśnięcia **2026-11-23**. Bez migracji do trwałego planu lub innego dostawcy dane klientów byłyby zagrożone. Sam darmowy web hosting nie wystarcza do wiarygodnej produkcji 24/7.

## Domena
Narzędzia Render dostępne w ChatGPT potwierdzają tworzenie usługi i wdrożenie, ale nie udostępniają zarządzania Custom Domains ani kontem rejestratora DNS.
1. Render: `msw-ai-com-portal` → Settings → Custom Domains → dodaj `msw-ai.com` i `www.msw-ai.com`.
2. Skopiuj **dokładne** wymagane rekordy DNS pokazane przez Render do panelu operatora domeny.
3. Poczekaj na status Verified i wystawienie HTTPS. Nie zmieniaj DNS `mobilny-serwis-warszawa.pl`.
4. Zweryfikuj logowanie, sesję, publiczne API i CSP po podłączeniu hosta.

## Model AI a darmowy agent
Silnik `local` naprawdę wykonuje zadania klasyfikacyjne, ale NIE jest modelem LLM ani wytrenowaną sztuczną siecią neuronową. Wywołania `openai` wymagają serwerowego `OPENAI_API_KEY`, `MSW_ALLOW_PAID_INFERENCE=1`, kontroli budżetu i jawnej zgody użytkownika przy każdym zadaniu. Nie podawaj kluczy klientowi ani w HTML.

## Zasady bezpieczeństwa
Bez automatycznego sterowania instalacjami krytycznymi, PLC, ECU, zamkami czy alarmami. Bez masowego publikowania spamu i nieautoryzowanego crawlowania. Bez hazardu, inwestycji, tokenów i obietnic zysku.
Publiczny profil bota nie jest automatycznie uznawany za zweryfikowany model. Ranking Areny to skuteczność konkretnych zadań klasyfikacji, nie uniwersalna inteligencja.

## Testy
`cd msw-ai-centrum-robotow && python -m unittest discover -s tests -p 'test_platform_core.py' -v`
CI: `.github/workflows/msw-ai-com-tests.yml`.

## Dalszy rozwój
- Oddzielny system autoryzacji właściciela i moderacji ofert, obsługa zgłoszeń i CRM.
- Wiarygodne benchmarki LLM z prywatnym zestawem testowym i budżetem API.
- Trwała pamięć RAG i źródła dokumentacji technicznej z prawami do wykorzystania.
- Pakiety kosztowe/komercyjne dopiero po przygotowaniu regulaminu, płatności i fakturowania.
- 300 profili i 500 operacji istnieją w osobnym module MSW Technical Exchange; nie są obecnie 300 uruchomionymi robotami i nie wolno wyświetlać ich jako takich.
