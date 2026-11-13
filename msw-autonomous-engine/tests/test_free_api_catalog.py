"""Offline unit checks for 20k discovery catalog. Run: python -m unittest discover -s tests -p 'test_free_api_catalog.py'"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "tools" / "free_api_catalog.py"
SPEC = importlib.util.spec_from_file_location("free_api_catalog", MODULE)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

class TestFreeApiCatalog(unittest.TestCase):
    def test_url_normalization_and_secrets(self):
        self.assertEqual(mod.normal_url("HTTPS://Example.COM/v1/"), "https://example.com/v1")
        self.assertIsNone(mod.normal_url("https://example.com/api?access_token=secret"))
        self.assertIsNone(mod.normal_url("http://127.0.0.1/"))
        self.assertIsNone(mod.normal_url("http://192.168.1.15/"))
        self.assertIsNone(mod.normal_url("https://user:pass@example.com/api"))
        self.assertIsNone(mod.normal_url("file:///etc/passwd"))
    def test_unique_unverified_and_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "input.json"
            src.write_text(json.dumps({"apis": [
                {"url":"https://api.example.org/v1", "source":"https://docs.example.org"},
                {"url":"https://API.EXAMPLE.ORG/v1/", "source":"https://docs.example.org"},
                {"url":"https://other.example.org/data"}
            ]}), encoding="utf-8")
            records, rejected = mod.collect([src])
            self.assertEqual(len(records), 2)
            self.assertEqual(rejected["duplicate_endpoint"], 1)
            self.assertTrue(all(not item["enabled"] and not item["zero_cost_verified"] for item in records))
            report = mod.write_catalog(records, rejected, Path(tmp) / "out")
            self.assertEqual(report["discovered_unique_urls"], 2)
            self.assertEqual(report["verified_apis"], 0)
            lines = (Path(tmp) / "out" / "catalog.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(all(json.loads(line)["discovery_status"] == "UNVERIFIED_SOURCE" for line in lines))

if __name__ == "__main__":
    unittest.main()
