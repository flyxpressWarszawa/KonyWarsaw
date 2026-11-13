# MSW — mapa systemów, priorytety, źródła prawdy i aktualność dowodów

**Data przekroju:** 2026-11-13. Jest to zapis architektoniczny, NIE aktywacja i NIE protokół nowej kolejki.

## Model działania

MSW to mobilny serwis wielobranżowy z obsługą premium w Warszawie i okolicach; centralny portal `mobilny-serwis-warszawa.pl`. Zakres usług: elektryka, hydraulika, ślusarstwo, spawanie, bramy/automatyka, CCTV i IP, domofony, smart home/IT, jacuzzi, sauna, rynny i serwis techniczny obiektów. Publiczna marka i kontakt muszą być spójne; nie wymyślać realizacji, zdjęć, opinii, cen i stanów magazynu. Historyczne przykłady cen nie stanowią automatycznie ofert na dziś.

Docelowo Hermes jest **koordynatorem**; po jego decyzji wykonanie odbywa się tylko przez istniejące, uprawnione silniki i kanały. `ChatGPT → istniejący podpisany transport → zweryfikowany odbiorca → wykonanie → trwały receipt + niezależny readback → raport`. Ogólny status serwisu, obecność modelu lub heartbeat nie wystarczają. Nie wprowadzać klonów Hermes, równoległych schedulerów, rejestrów satelitów, kolejek lub niepotwierdzonych adapterów.

## Źródła prawdy i rozdział odpowiedzialności

| Obszar | System nadrzędny | Bezpieczny warunek uznania wykonania |
|---|---|---|
| Satelity i parowanie | Żywy rejestr COMBO | Autoryzowany odczyt właściwej domeny + podpis + wersja + indywidualny ACK |
| Kolejka dyrektora i fabryka produktów | Istniejące Cortex / Native Factory / podpisane kolejki | Identyfikator zlecenia, stan lease, receipt wykonawcy, niezależny odczyt efektu |
| Produkty i sprzedaż | Właściwy WordPress / WooCommerce | Status, SKU, uprawnienia, ZIP i SHA na serwerze, opłacone zamówienie i test pobrania |
| SEO / popyt / koszty | GSC, własne pomiary Woo, Opportunity250 i inne legalne źródła | Źródło, okres, zakres, deduplikacja, koszt, realny pomiar; inaczej NULL |
| Kod i historia zmian | GitHub z CAS i przeglądem CI | Commit/branch, test, niezależny status wdrożenia, rollback |
| Worker | Istniejący Render MSW/Hermes | Live deployment + log wybranej wersji + trwały receipt konkretnej pracy |
| Prywatne źródła wiedzy | Uprawniona pamięć MSW / prywatne WordPress / istniejący trwały storage workera | Odczyt źródła, SHA, zgoda, zapis, odczyt zwrotny i indywidualne ACK |

## Znane komponenty i status historyczny

**WordPress/COMBO/Cortex.** Historyczne migawki różnią się liczebnością i wersjami; najnowszy raport 2026-11-13 podał 333 zainstalowane wtyczki, Cortex 3.9.1.1, COMBO 108.1.5, Native Factory 1.2.1.1 i Bridge 1.1.9. Nieaktywne wtyczki pozostają nieaktywne; każda wtyczka wymaga osobnej weryfikacji capability, scope i uprawnień. COMBO raportuje deklarowane 41 satelitów; liczba ta jest historyczna/punktowa i nie potwierdza każdego indywidualnego ACK. Wykluczonej domeny `dom-ai.pl` nie otwierać bez nowej autoryzacji; fitness tylko w dostępnym zakresie odczytu.

**Render/Hermes.** Repozytorium robocze: `flyxpressWarszawa/KonyWarsaw`, ścieżka `msw-autonomous-engine`, gałąź `msw/engine-runtime-20261104`; worker startuje `python -m msw_engine`. Dokument `BUSINESS-BRIDGE.md` jest protokołem istniejącego mostu, a `AGENTS.md` ogranicza samoczynne deploymenty. Istniejący worker działa poza komputerem właściciela. Snapshot z 13.11 raportował live deploy oraz readback 22 334 wpisów źródłowego katalogu jednego zadania, lecz błędy `business_http_503` i `knowledge_effect_contract_required` blokowały uznanie niektórych efektów. Nie restartować serii deploymentów w toku, nie zwiększać planu.

**Hostingi.** GoDaddy/cPanel: historycznie 5 agentów serwerowych HMAC, bez wiarygodnego potwierdzenia pełnej natywnej kontroli 5/5 przez API; centralny WordPress bywał przeciążony. Kierunek to przenoszenie kosztownych obliczeń do autoryzowanych workerów i istniejących wolnych zasobów, nie automatyczne udostępnianie plików klientom ani przydzielanie niepotwierdzonych uprawnień. Doktor może diagnozować, ale każda naprawa wymaga bezpiecznej autoryzacji, kopii, limitu zasobów i rollbacku.

**Kolejki.** Delegowany callback `MSW_Module_Autonomy::capture` jest zamierzony — nie dodawać drugiego callbacka. Historyczny Factory #586 i niepewny tick wymagają dotarcia do własnej kolejki oraz readbacku; status `unknown` nie oznacza wykonania. Nowe wiadomości serwera powinny być idempotentne, podpisane, z lease i jednoznacznym odbiorcą.

**Modele i API.** System ma wiele integracji usług AI, ale nazwa w rejestrze kluczy nie dowodzi dostępnego modelu ani limitu. Używać wyłącznie właściwie autoryzowanych API i zgodnie z warunkami dostawców; preferować legalne darmowe fallbacki i lokalne modele po teście jakości, limitów oraz kosztu. Alibaba Token Plan dla interaktywnego CLI nie jest tym samym co produkcyjne Model Studio API. Własny SLM/Owned LLM i QNN nie są dowodem zakończonego treningu; poprawę trzeba wykazać na niezależnym teście.

**SEO/marketing.** Business Hunter nadzoruje istniejący cykl godzinowy oraz odrębne raporty 11:00/20:00 — nie tworzyć nowych harmonogramów. Na początku czytać Opportunity250 i zaległe P0/P1; zero wiarygodnych nowych okazji raportować wprost. Dane GSC to wyświetlenia w wyszukiwaniu, nie wyświetlenia produktów; brak atrybucji lub kosztu = NULL, nie 0. Zakaz niezweryfikowanych nowych płatnych badań Similarweb/DataForSEO, zmian budżetów i cen.

**Klienci/Telegram.** Tryb AI tylko opt-in, dane kontaktowe redagować, nie przenosić rozmów klientów do wspólnego RAG. WordPress zachowuje uprawnienia, tokeny i chat_id; Render nie wybiera odbiorcy sam. ACK Telegram i storage readback są warunkiem stwierdzenia dostarczenia. Żadnych testowych opłat PayPal bez odrębnej, konkretnej ścieżki kontroli.

## Kolejność priorytetów

P0: ciągłość/bezpieczeństwo serwera, P1: stary nierozliczony Factory/transport/ACK, P2: prawdziwe pomiary i QA rozpoczętych 3 produktów, P3: nowe okazje i adaptacje bez duplikacji. Incydent z 404 katalogu nie musi oznaczać niedostępności domeny; 503 nie wolno automatycznie ponawiać jako POST. Własność katalogu, blokady i historia są utrzymywane przez istniejącego właściciela danych.

## Zabezpieczenia transferu wiedzy

Publiczny GitHub zawiera tylko tę mapę. Prywatne korpusy, HMAC, płatności i rozmowy nie mogą tu trafić. Dokładne byte-for-byte treści ze źródła do Hermesa przesyłać tylko przez już autoryzowany kanał prywatny, po wykluczeniu materiałów wrażliwych i weryfikacji rzeczywistego efektu. Wszystkie `readback_verified=false` i `engine_ack_verified=false` zachować jako brak dowodu, nie naprawiać wartości przez wpisanie `true` w pliku.
