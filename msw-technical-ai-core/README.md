# MSW Technical AI Core v5 — niezależny silnik edukacyjny i integracyjny

**Architektura:** samodzielny FastAPI/Render + OpenAPI, nie jest wtyczką WordPress. Publiczne API tylko do odczytu; autoryzacja agentów, egzaminy i publikowanie zatwierdzonej wiedzy nadal po stronie istniejącego MSW Technical University 1.0 (WordPress).

## Zakres zweryfikowanego pakietu

- 40 rodzin wiedzy; **1000 autorskich specjalizacji operacyjnych MSW**, inspirowanych formalnymi klasyfikacjami (nie 1000 oficjalnych dyscyplin UNESCO lub ACM).
- 1000 **profili agentów** (szablony, nie działające procesy ani konta w zewnętrznych LLM).
- 1200 lekcji + 1600 laboratoriów: 20 obszarów po 60 lekcji i 80 laboratoriów, opracowania redakcyjne ze scenariuszami i kryteriami dowodowymi; nie są to pomiary przeprowadzone w rzeczywistych instalacjach.
- 61 kalkulatorów i przeliczników JS wykonywanych w przeglądarce, z walidacją danych.
- Wykresy techniczne oparte na rzeczywistej liczbie pozycji katalogowych, bez fałszywych rankingów mocy.
- Publiczne endpointy `/api/v1/status`, `/api/v1/families`, `/api/v1/specializations`, `/api/v1/agents`, `/api/v1/curriculum`, `/api/v1/tools`, `/api/v1/research`, `/api/v1/engines`, `/api/v1/quantum/providers`, `/api/v1/quantum/bell`, `/api/v1/paths`, `/.well-known/msw-agent.json` i `/openapi.json`.
- Dzienny workflow GitHub Actions: 6 źródeł z listy 16 oficjalnych repozytoriów w rotacji, zapis **wyłącznie bibliografii do weryfikacji**. Zaplanowanie workflow nie jest potwierdzeniem faktycznego uruchomienia — trzeba sprawdzić zakładkę Actions.

## IBM, Google, NASA i fotonika

- Google Quantum AI: symulacja Cirq jest lokalnie dostępna po doinstalowaniu frameworku; fizyczne procesory Google wymagają dopuszczenia do programu (nie skonfigurowano dostępu).
- IBM Quantum: wymagane konto IBM Cloud, instancja usługi i API key. Nie wgrywaj klucza do GitHub. Ten silnik **nie jest połączony z IBM QPU**.
- Xanadu/PennyLane: można korzystać z symulatorów; fizyczny sprzęt fotoniczny tylko według aktualnych uprawnień dostawcy.
- NASA: publiczne źródła naukowe/danych, **brak potwierdzonej ogólnodostępnej usługi QPU NASA**.
- `/api/v1/quantum/bell` oblicza analityczny, 2-kubitowy rozkład stanu Bella na CPU; nie wykonuje żadnych zdalnych zadań kwantowych.

## Model bezpieczeństwa

Brak publicznych endpointów POST umożliwiających sterowanie instalacjami. Nie udostępnia się danych klientów, kluczy, serwisu bram/ECU/PLC. Wiadomości do zewnętrznych robotów i materiały od nich wymagają właściciela, uwierzytelnienia i moderacji na WordPressie; API tutaj podaje jedynie profil/katalog.

## Wdrożenie Render

Build root: `msw-technical-ai-core/`; `Dockerfile` jest także kopiowany do korzenia repozytorium jako `MSWTechnicalCore.Dockerfile` jeśli panel potrzebuje niestandardowej ścieżki. Region Frankfurt, plan free tylko do testów (zasób może przechodzić w uśpienie). Ustaw `dockerContext='./msw-technical-ai-core'`, `dockerfilePath='./Dockerfile'` i sprawdź log pierwszego wdrożenia. Repozytorium GitHub zawiera katalog `msw-technical-ai-core/`, w którym umieszczono powyższe pliki. Dzienny importer działa niezależnie w GitHub Actions, a API wystawia zapisane metadane po ponownym deployu.

## Statusy do rozróżnienia

- `read_only_technical_knowledge`: API gotowe do wyszukiwania materiałów.
- `unreviewed_public_source_reference`: link odkryty w oficjalnym kanale, nie jest zatwierdzonym materiałem uczelni.
- `running_agents_proven: 0`: nie obiecujemy uruchomienia 1000 procesów.
- `quantum_hardware_connected: false`: brak fizycznych zdalnych obliczeń kwantowych.
- Weryfikację skuteczności modeli, połączeń z WordPressem i uprawnień przeprowadza się niezależnie od testów jednostkowych.
