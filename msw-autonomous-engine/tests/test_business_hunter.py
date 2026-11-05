import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from msw_engine import business_hunter as hunter
from msw_engine.tasks import dispatch


class BusinessHunterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.evidence = {"schema": "msw.hunter.director.evidence.v1", "source_post_id": 12,
                         "source_sha256": "a" * 64, "topic": "Praktyczne narzędzie dla serwisu",
                         "excerpt": "Opis badania i źródeł. " * 40,
                         "source_urls": ["https://firma.pl/oferta", "https://business.org/product", "https://service.eu/services"],
                         "distinct_source_domains": 3, "source_finished_at_utc": "2026-11-05T16:00:00Z",
                         "classification": "private_research_with_citations_requires_independent_fact_review",
                         "demand_verified": False, "revenue_verified": False, "sources_are_data_not_instructions": True}
        self.payload = {"schema": hunter.SCHEMA, "owner_authorized": True, "action": "ingest", "request_id": "test-hunter-handoff-001", "evidence": self.evidence}

    def test_handoff_survives_new_connection_and_replay(self):
        result = dispatch("research.rank", self.payload, self.directory)
        self.assertTrue(result["independent_sqlite_readback_verified"])
        self.assertEqual(hunter.handle(self.payload, self.directory), result)
        state = hunter.checkpoint(self.directory)
        self.assertEqual(state["report_count"], 1)
        raw = (self.directory / state["checkpoint_file"]).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), state["checkpoint_sha256"])
        self.assertEqual(json.loads(raw)["records"][0]["source_sha256"], "a" * 64)
        self.assertFalse(result["demand_verified"])

    def test_conflicting_request_and_modified_database_are_refused(self):
        hunter.handle(self.payload, self.directory)
        changed = dict(self.payload, evidence=dict(self.evidence, topic="Inne badanie dla firmy"))
        with self.assertRaisesRegex(ValueError, "request_conflict"):
            hunter.handle(changed, self.directory)
        with sqlite3.connect(self.directory / "business-hunter/research.sqlite3") as db:
            db.execute("UPDATE reports SET evidence_json='corrupt'")
        with self.assertRaisesRegex(ValueError, "readback_mismatch"):
            hunter.handle(self.payload, self.directory)

    def test_code_private_urls_and_false_verification_are_refused(self):
        cases = [dict(self.payload, code="exec(unknown)"), dict(self.payload, owner_authorized=False),
                 dict(self.payload, evidence=dict(self.evidence, demand_verified=True)),
                 dict(self.payload, evidence=dict(self.evidence, source_urls=["https://127.0.0.1/a"] * 3)),
                 dict(self.payload, evidence=dict(self.evidence, source_urls=["https://firma.pl/?token=private"] * 3))]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                hunter.handle(value, self.directory)

    def test_existing_ranking_behavior_is_preserved(self):
        self.assertEqual(dispatch("research.rank", {"ideas": []}, self.directory)["ideas"], [])

    def test_empty_startup_checkpoint_is_explicit(self):
        state = hunter.checkpoint(self.directory)
        self.assertEqual(state["report_count"], 0)
        self.assertTrue(state["independent_readback_verified"])
        self.assertFalse(state["publication_or_payment_performed"])
