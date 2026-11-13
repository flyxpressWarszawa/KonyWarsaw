"""Offline contract tests for the MSW API 20k discovery harvester."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "tools" / "harvest_api20k.py"
SPEC = importlib.util.spec_from_file_location("harvest_api20k", PATH)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

class TestSourceDiscovery(unittest.TestCase):
    def test_reject_private_secret_or_bad_urls(self):
        for url in ("http://127.0.0.1:8080/a", "http://10.0.0.10/api",
                    "http://192.168.1.4/x", "https://localhost/test",
                    "https://server.local/api", "file:///tmp/a",
                    "https://site.example/foo?access_token=SECRET",
                    "https://alice:password@site.example/x"):
            with self.subTest(url=url):
                self.assertIsNone(mod.safe_url(url))
        self.assertEqual(mod.safe_url("HTTPS://Example.org/v1/"), "https://example.org/v1")

    def test_curated_flags_are_only_claims(self):
        c = mod.Catalog()
        c.ingest_apidirectory({"meta":{"records":2},"apis":[
            {"slug":"alpha","name":"Alpha","docsUrl":"https://alpha.example/docs",
             "auth":"None","pricing":"Free","category":"data"},
            {"slug":"beta","name":"Beta","docsUrl":"https://beta.example/docs",
             "auth":"API Key","pricing":"Freemium"}]})
        self.assertEqual(len(c.items), 2)
        self.assertEqual(c.directory_claims_free_no_auth, 1)
        for row in c.items.values():
            self.assertEqual(row["status"], "DISCOVERED_UNVERIFIED")
            self.assertFalse(row["pricing_for_msw_verified"])
            self.assertFalse(row["no_login_verified"])
            self.assertFalse(row["enabled"])

    def test_apis_guru_and_swaggerhub_import(self):
        c = mod.Catalog()
        c.ingest_apis_guru({"example.com:widgets":{"preferred":"v1","versions":{
            "v1":{"info":{"title":"Widgets"},"swaggerUrl":"https://api.apis.guru/v2/specs/example.com/widgets.json"}}}})
        received, added = c.ingest_swaggerhub_page({"apis":[
            {"name":"Catalog","owner":"vendor","url":"https://api.swaggerhub.com/apis/vendor/catalog/1.0"},
            {"name":"Internal","owner":"vendor","url":"http://127.0.0.1/spec"}]})
        self.assertEqual((received, added), (2, 1))
        self.assertEqual(len(c.items), 2)

    def test_dedup_and_jsonl_counts(self):
        c = mod.Catalog(max_candidates=3)
        c.add(source="x", identity="1", name="A", docs="https://example.com/docs")
        self.assertFalse(c.add(source="x", identity="1", name="A", docs="https://example.com/docs"))
        c.add(source="x", identity="2", name="B", docs="https://example.com/docs2")
        with tempfile.TemporaryDirectory() as tmp:
            report = c.write(Path(tmp))
            self.assertEqual(report["candidate_registry_entries"], 2)
            self.assertEqual(report["independently_verified_free_no_auth"], 0)
            self.assertEqual(report["live_hermes_inference_verified"], 0)
            file = Path(tmp) / "candidates-001.jsonl"
            lines = file.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(all(not json.loads(x)["enabled"] for x in lines))
            self.assertEqual(len(report["sha256_shards"]), 1)

if __name__ == "__main__":
    unittest.main()
