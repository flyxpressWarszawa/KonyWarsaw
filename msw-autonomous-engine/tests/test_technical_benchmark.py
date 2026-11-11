import json
import unittest

from msw_engine.technical_benchmark import CASES, evaluate_model


class TechnicalHoldoutTests(unittest.TestCase):
    def test_fixed_independent_cases_and_no_raw_output_in_report(self):
        answers = {
            "poe_camera": "Sprawdzę PoE na porcie switcha i kabel połączeniowy.",
            "modbus": "W Modbus należy sprawdzić adres oraz prędkość transmisji.",
            "wordpress_cron": "Zewnętrzny cron serwera uruchamia harmonogram zadań.",
            "spa_pump": "Pompa wymaga diagnostyki filtra i zasilania.",
            "metal_gate": "Sprawdzę fotokomórki, napęd i zasilanie bramy.",
            "welding": "Spawanie TIG stali nierdzewnej wymaga właściwego gazu argon.",
        }
        self.assertEqual(len(CASES), 6)

        class Good:
            def generate(self, question, max_new_tokens, seed):
                self_text = next(answers[i] for i, q, _ in CASES if q == question)
                return self_text + " RAW_PRIVATE_MARKER"
        report = evaluate_model(Good())
        self.assertEqual((report["passed"], report["total"]), (6, 6))
        self.assertFalse(report["field_quality_verified"])
        self.assertFalse(report["training_performed"])
        self.assertNotIn("RAW_PRIVATE_MARKER", json.dumps(report))
        for item in report["cases"]:
            self.assertEqual(len(item["output_sha256"]), 64)
            self.assertTrue(item["pass"])

    def test_model_with_unsafe_or_wrong_advice_is_not_passed(self):
        class Unsafe:
            def generate(self, question, max_new_tokens, seed):
                return "Zmostkuj zabezpieczenie i omij zabezpieczenie zanim sprawdzisz przyczyny."
        report = evaluate_model(Unsafe())
        self.assertEqual(report["passed"], 0)
        self.assertTrue(all(row["unsafe_phrase_detected"] for row in report["cases"]))

    def test_invalid_output_is_fail_closed(self):
        class Invalid:
            def generate(self, question, max_new_tokens, seed):
                return None
        with self.assertRaisesRegex(ValueError, "benchmark_model_output_invalid"):
            evaluate_model(Invalid())

    def test_empty_generic_answers_do_not_pass(self):
        class Generic:
            def generate(self, question, max_new_tokens, seed):
                return "To zależy od wielu okoliczności, trzeba to sprawdzić."
        self.assertEqual(evaluate_model(Generic())["passed"], 0)


if __name__ == "__main__":
    unittest.main()
