"""MSW AI — technical-only rule engine and reproducible arena benchmarks.
This module NEVER executes commands on machines, visits arbitrary URLs or claims
to be a trained language model. AI-provider adapters live in platform_main.py.
"""
from __future__ import annotations
import re
from typing import Any

DOMAINS = {
    "ELEKTRYKA": ("elektryka", ["prąd", "prad", "gniazd", "rozdziel", "bezpiecznik", "zwarcie", "rcd", "napięcie"]),
    "HYDRAULIKA": ("hydraulika", ["rur", "wyciek", "wod", "ciśnien", "cisnien", "zawór", "zalanie", "pompa"]),
    "SPAWANIE": ("spawanie", ["spaw", "tig", "mig", "mag", "elektrod", "aluminium", "stal", "żeliwo"]),
    "SLUSARSTWO": ("ślusarstwo", ["zamek", "wkładka", "klucz", "drzwi", "zamek szyfrowy", "elektrozaczep"]),
    "SPA": ("jacuzzi i sauna", ["jacuzzi", "saun", "hydromasaż", "grzałk", "basen", "balboa", "gecko"]),
    "SMART_HOME": ("inteligentny dom", ["zigbee", "z-wave", "matter", "thread", "knx", "domofon", "smart home", "kamera"]),
    "POJAZDY": ("pojazdy i EV", ["obd", "ecu", "can", "silnik", "pojazd", "ładowark", "akumulator", "samoch"]),
    "MASZYNY": ("automatyka maszyn", ["plc", "scada", "modbus", "opc ua", "sterownik", "siłownik", "pneumatyk", "maszyn"]),
    "SIECI": ("sieci IT", ["ethernet", "vlan", "router", "switch", "sieć", "siec", "wifi", "poe", "dns"]),
    "ENERGIA": ("energia i HVAC", ["fotowolta", "inwerter", "hvac", "pompa ciepła", "wentylac", "klimatyzac", "falownik"])
}
CRITICAL = (
    "dym", "pożar", "pozar", "poraż", "poraz", "iskrzenie", "gaz", "cofanie spalin",
    "porażenie", "zagraża życiu", "zagrożenie życia", "płonie", "plonie", "zapach spalenizny",
)
ALLOWED_ROLES = {"diagnosta", "analityk", "badacz", "serwisant", "integrator"}
ALLOWED_ENGINES = {"local", "openai"}
PROMPTS = {
    "ELEKTRYKA": ["Czy występuje napięcie i czy pracę sprawdził uprawniony elektryk?", "Jaki model rozdzielnicy lub urządzenia?"],
    "HYDRAULIKA": ["Skąd dokładnie występuje wyciek?", "Jaki jest typ instalacji i model elementu?"],
    "SPAWANIE": ["Jaki materiał i jaka grubość elementu?", "Jakie są dostępne metody łączenia?"],
    "SLUSARSTWO": ["Jaki model zamka i rodzaj mechanizmu?", "Czy posiadasz uprawnienie do obsługi zamka?"],
    "SPA": ["Jaki sterownik i model urządzenia?", "Czy urządzenie zgłasza kod błędu?"],
    "SMART_HOME": ["Jaki producent oraz protokół urządzeń?", "Czy dostępne są logi i dokumentacja?"],
    "POJAZDY": ["Jaki model, rocznik i kod DTC?", "Czy test odbywa się na postoju?"],
    "MASZYNY": ["Jaki model PLC, numer błędu i kontekst procesu?", "Czy maszyna została zabezpieczona przez operatora?"],
    "SIECI": ["Jaka topologia i wersja firmware?", "Czy pomiary wykonano za zgodą właściciela sieci?"],
    "ENERGIA": ["Jaki model urządzenia i wartości nominalne?", "Czy występują alarmy bezpieczeństwa?"],
}
CHALLENGES = [
    ("W rozdzielnicy występuje zapach spalenizny i napięcie.", "ELEKTRYKA"),
    ("Zawór główny przecieka, woda zalewa pomieszczenie.", "HYDRAULIKA"),
    ("Należy naprawić pęknięty spaw TIG konstrukcji aluminiowej.", "SPAWANIE"),
    ("Klucz nie obraca wkładki zamka drzwi.", "SLUSARSTWO"),
    ("Sterownik jacuzzi Balboa wyświetla kod błędu.", "SPA"),
    ("Brama Zigbee nie łączy czujników Smart Home.", "SMART_HOME"),
    ("Pojazd zgłasza kod DTC przez OBD-II.", "POJAZDY"),
    ("Sterownik PLC nie aktualizuje wartości SCADA.", "MASZYNY"),
    ("Przełącznik PoE nie zasila urządzeń sieciowych.", "SIECI"),
    ("Pompa ciepła HVAC raportuje błędny parametr.", "ENERGIA"),
]
def classify(text: str, role: str = "diagnosta") -> dict[str, Any]:
    if not isinstance(text, str) or not 3 <= len(text.strip()) <= 2500:
        raise ValueError("Opis powinien mieć od 3 do 2500 znaków")
    normalized = text.lower()
    ranked = {name: sum(3 if term in normalized else 0 for term in terms) for name, (_, terms) in DOMAINS.items()}
    primary = max(ranked, key=ranked.get)
    score = ranked[primary]
    critical = any(term in normalized for term in CRITICAL)
    if not score: 
        return {"status": "insufficient_information", "category": None, "confidence": "none",
                "warning": "Zbyt mało danych do przypisania specjalizacji.",
                "questions": ["Podaj producenta, model, objawy i komunikat błędu."], "model_used": False}
    label = DOMAINS[primary][0]
    return {
        "status": "requires_specialist" if critical else "triage_only",
        "category": primary, "category_label": label,
        "confidence": "heuristic_not_probability",
        "risk": "critical_possible" if critical else "not_assessed",
        "questions": PROMPTS[primary],
        "suggested_action": ("Zabezpiecz miejsce i zleć ocenę uprawnionemu specjaliście; "
                             "nie prowadź dalszych prób na urządzeniu." if critical
                             else "Zapisz model urządzenia, kod błędu i pomiary; zweryfikuj hipotezy z dokumentacją."),
        "warning": ("Wynik regułowy służy do wstępnej klasyfikacji; nie wykonuj działań "
                    "przy urządzeniach elektrycznych, gazowych, pojazdach ani PLC bez uprawnień."),
        "role": role, "model_used": False,
    }
def benchmark(label: str = "msw-core") -> dict[str, Any]:
    """Run real, deterministic tests. No score is fabricated or called general AI IQ."""
    rows = []
    for question, expected in CHALLENGES:
        predicted = classify(question).get("category")
        rows.append({"case": len(rows) + 1, "expected": expected, "actual": predicted, "pass": expected == predicted})
    passed = sum(row["pass"] for row in rows)
    return {"engine": label, "trials": len(rows), "passed": passed,
            "accuracy_percent": round(100 * passed / len(rows), 2),
            "type": "local_diagnostic_classification_not_model_power", "cases": rows}
def technical_domain(value: str) -> str:
    if value not in DOMAINS:
        raise ValueError("Wybierz jedną z zatwierdzonych dziedzin technicznych")
    return value
