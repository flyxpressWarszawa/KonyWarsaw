# MSW × Alibaba — automatyczne wsparcie nauki: procedura bezpiecznego uruchomienia

Stan 2026-11-11: **PR DRAFT / NIE WDROŻONO**. Zmiany na gałęzi są testowe i nie zmieniają
Render, WordPress, Hermesa ani usług Alibaba. Nie tworzono kluczy i nie generowano
płatnych wywołań.

## Co już istnieje

- `msw-central-engine-worker` na Renderze: `python -m msw_engine`, jedna kolejka
  WordPress/Hermes, podpisane claim/heartbeat/complete/status, SHA i readback.
- `msw_engine.federation` oraz `aliyun_direct` i `aliyun_protocols`: adapter
  Model Studio (Singapore), z idempotentnym `request_id` i rejestrem wyników.
- `msw_engine.tasks.knowledge_ingest`: zweryfikowany zapis do SQLite/FTS
  (RAG, nie aktualizacja wag).
- `msw_engine.own_model`: odseparowany, autoryzowany trening własnego modelu,
  z istniejących dopuszczonych checkpointów i odczytem kontrolnym.
- Interaktywna praca w Codex CLI: Qwen 3.8 Max z osobistym Token Plan,
  rozumowanie `medium`. **Nie wolno** przenieść tego klucza do backendu.

## Jeden brakujący krok właściciela na koncie Alibaba

Zgodnie z dokumentacją Alibaba potrzebne jest aktywowanie
**Alibaba Cloud Model Studio + Large Model Inference** w regionie Singapore,
zaakceptowanie warunków usługi i naliczanych tokenów, a następnie utworzenie
**osobnego API Key Model Studio Pay-as-you-go**. Nie potrzeba wykupywać
drugiego wielomiesięcznego planu — usługa nalicza koszt za faktyczne wywołania,
więc wymaga osobnego limitu budżetu i monitoringu.

Panel: https://modelstudio.console.alibabacloud.com/
Oficjalna instrukcja: https://www.alibabacloud.com/help/en/model-studio/activate-alibaba-cloud-model-studio

**Przebieg:** konto Alibaba → Singapore → Model Studio → Activate / Confirm
Activation, jeśli nieaktywne → zatwierdź umowę → Large Model Inference →
Model Studio API Keys → Create API Key dla tego samego workspace.
Tego etapu nie da się ukończyć za użytkownika bez uwierzytelnionego dostępu
do konta i zgody na warunki finansowe. Nie wysyłać klucza do czatu ani GitHub.

Po utworzeniu, sekretem `MSW_ALIYUN_API_KEY` (odrębnym od `sk-sp-`) zarządza
wyłącznie **prywatny** magazyn sekretów istniejącego workera Render.
`MSW_ALIYUN_MODEL` to dozwolony model, `MSW_PAID_AI_TRANSPORT=direct_aliyun`
i `MSW_ALIYUN_PROTOCOL` wybierają istniejący adapter.
**Nie aktywuj ich automatycznie bez kontroli kosztów, STOP/HOLD i bez
pozytywnego testu na nieprodukcyjnym stanowisku**. Dotychczasowe błędy
`403 AccessDenied.Unpurchased` nie oznaczają, że Token Plan jest wadliwy:
to inny sposób rozliczenia i może wymagać aktywacji lub praw do modelu.

## Co znaczy „Alibaba trenuje nasze silniki”

1. **Warstwa wiedzy:** zatwierdzone dane techniczne MSW są indeksowane
   z poświadczeniem źródła i odczytem kontrolnym przez obecną
   `knowledge.ingest`. To przyrost RAG, NIE trening wag.
2. **Warstwa recenzji:** Model Studio Pay-as-you-go (po aktywacji) może
   analizować proponowane ulepszenia silników, zadania techniczne i
   testy, przekazując propozycje do tej samej kolejki oraz Hermesa.
   Odpowiedź Qwen nie jest dowodem prawdy ani pozwoleniem do wdrożenia.
3. **Warstwa własnych modeli:** istniejący `own_model.train` może
   trenować własne wagi tylko przy odpowiednim, zatwierdzonym zestawie
   danych/checkpointów. Trening `MSW OWN` nie oznacza, że Qwen sam
   zmienił wagi.
4. **Warstwa fine-tuning Alibaba:** Model Studio Singapore obsługuje
   SFT m.in. `qwen3.8-27b`, nie należy zakładać możliwości
   fine-tune `qwen3.8-max`. Płatny trening wymaga osobnej decyzji o
   modelu, danych, liczbie epok, budżecie, ewaluacji i wdrożeniu.
   Przygotowanie pliku nie rozpoczyna treningu.

## Gotowy bezpieczny etap offline

`telemetry/prepare_qwen_sft_offline.py` przygotowuje (po jednoznacznym
uruchomieniu) prywatne pliki ChatML JSONL do potencjalnego przyszłego
SFT. Wymaga jawnych poświadczeń `rights_verified`,
`privacy_verified`, `owner_approved`, `technical_reviewed`,
prawidłowego SHA-256 źródła i klasyfikacji
`owner_approved_nonpersonal`. Pomocnicze dopasowanie wykrywa typowe
sekrety i dane kontaktowe, ale nie zastępuje niezależnego przeglądu RODO.

- Bez `--private-output-dir`: tylko liczba sprawdzonych przykładów,
  bez zapisu danych.
- Z `--private-output-dir`: lokalne prywatne train/validation JSONL,
  bez wysyłania do Alibaba, bez inicjowania treningu.
- Zakaz automatycznego pobierania zdjęć FotoAtlasu, korespondencji,
  faktur, numerów telefonów i danych klientów.
- Eksport jedynie z wcześniej potwierdzonych materiałów, nie
  z automatycznie wymyślonych przykładów.

Oficjalna dokumentacja SFT ChatML:
https://www.alibabacloud.com/help/en/model-studio/text-generation-tuning-data-upload-rules
Lista modeli/regionów:
https://www.alibabacloud.com/help/en/model-studio/model-training-overview

## Bezpieczne przełączenie na pełną pracę — tylko po bramkach

1. Administrator Alibaba zatwierdza aktywację PAYG i budżet.
2. Klucz PAYG trafia wyłącznie do Render secrets; bez udostępnienia
   użytkownikom i bez ekspozycji w ChatGPT. Nowy
   `aliyun_billing_guard` odrzuca osobisty Token Plan dla backendu.
3. Sprawdzenie uprawnień modelu + jeden niski test kontrolny, który
   użytkownik jawnie autoryzuje; wynik musi mieć trwale zapisany hash
   i podpisane ACK/readback w obecnej kolejce.
4. Po przejściu B05/B06/B08, lease/heartbeat/idempotency i backup,
   włączyć ograniczoną **istniejącą** ścieżkę `llm.generate` oraz
   nadzór kosztów i przerwanie po przekroczeniu pułapu.
5. Zbierać poprawne, prawa/RODO/technicznie zatwierdzone przykłady,
   ewaluować jakość przeciw stałemu benchmarkowi, potem zdecydować
   czy fine-tune w Alibaba rzeczywiście poprawia wyniki.
6. Dopiero osobno autoryzować płatny trening SFT/LoRA i deployment.

**Bez przyznanych uprawnień, działającego prawidłowego klucza i
zatwierdzonego budżetu = STOP dla automatycznych wywołań i treningu.**
Nie tworzyć drugiej głównej kolejki ani nowego workera i nie zmieniać
`python -m msw_engine`.
