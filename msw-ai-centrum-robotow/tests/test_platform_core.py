import os
import unittest
from app.platform_core import DOMAINS, classify, benchmark

class TestTechnicalCore(unittest.TestCase):
    def test_domains(self):
        self.assertEqual(len(DOMAINS), 10)
    def test_classification(self):
        self.assertEqual(classify("Sterownik PLC nie aktualizuje wartości SCADA.")["category"], "MASZYNY")
        self.assertEqual(classify("Pojazd zgłasza kod DTC przez OBD-II.")["category"], "POJAZDY")
    def test_critical(self):
        self.assertEqual(classify("Dym i zapach spalenizny w rozdzielnicy")["risk"], "critical_possible")
    def test_heuristic_benchmark(self):
        result=benchmark()
        self.assertEqual(result["trials"],10)
        self.assertEqual(result["passed"],sum(x["pass"] for x in result["cases"]))
    def test_no_unauthorized_device_commands(self):
        self.assertFalse(classify("Sterownik PLC nie aktualizuje wartości SCADA.")["model_used"])

if __name__ == "__main__":
    unittest.main()
