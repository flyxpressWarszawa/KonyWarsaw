import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from msw_engine.legacy_memory import restore, search
from msw_engine.runtime_pack import unpack
from msw_engine.tasks import knowledge_ingest, knowledge_search, TaskError


class LegacyMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous = os.environ.pop("MSW_RUNTIME_PACK_ROOT", None)

    def tearDown(self):
        os.environ.pop("MSW_RUNTIME_PACK_ROOT", None)
        if self.previous is not None:
            os.environ["MSW_RUNTIME_PACK_ROOT"] = self.previous
        self.temp.cleanup()

    def configure(self, *, bad_digest=False, wrong_count=False, extra_field=False):
        row = {"id": 1, "title": "LegacyFixtureUnique", "content": "Source text requiring factual review", "source": "old/source.json", "kind": "legacy"}
        if extra_field:
            row["unapproved_schema_extension"] = True
        raw = (json.dumps(row, ensure_ascii=False) + "\n").encode()
        compressed = lzma.compress(raw)
        info = {"source_archive_sha256": "a" * 64, "uncompressed_sha256": hashlib.sha256(raw).hexdigest(),
                "records": 2 if wrong_count else 1, "uncompressed_bytes": len(raw), "review_state": "legacy_unreviewed",
                "model_weights_included": False, "compressed_sha256": "0" * 64 if bad_digest else hashlib.sha256(compressed).hexdigest()}
        files = {"malta-memory/corpus-manifest.json": json.dumps(info).encode(), "malta-memory/knowledge.jsonl.xz": compressed}
        manifest = {"schema": "msw.runtime-pack.v1", "modules": [{"id": "malta-source-memory", "root": "malta-memory", "version": "3.1.0", "source_sha256": "a" * 64}],
                    "files": {name: hashlib.sha256(body).hexdigest() for name, body in files.items()}}
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, body in files.items():
                archive.writestr(name, body)
            archive.writestr("msw-runtime-pack.json", json.dumps(manifest))
        pack = stream.getvalue()
        root, _ = unpack(pack, hashlib.sha256(pack).hexdigest(), self.root)
        os.environ["MSW_RUNTIME_PACK_ROOT"] = str(root)

    def test_restored_source_is_persistent_and_explicitly_unreviewed(self):
        self.configure()
        result = restore(self.root)
        self.assertEqual((result["state"], result["records"]), ("ready", 1))
        found = search({"query": "LegacyFixtureUnique"}, self.root)
        self.assertEqual(found["items"][0]["id"], "legacy:malta:1")
        self.assertEqual(found["items"][0]["review_state"], "legacy_unreviewed")
        self.assertFalse(found["training_performed"])
        reopened = restore(self.root)
        self.assertTrue(reopened["cache_reused"])
        self.assertEqual(reopened["database_sha256"], result["database_sha256"])

    def test_default_approved_search_does_not_mix_legacy_sources(self):
        self.configure()
        restore(self.root)
        knowledge_ingest({"records": [{"id": "approved:1", "title": "Approved source", "text": "Different technical material", "approved": True}]}, self.root)
        self.assertEqual(knowledge_search({"query": "LegacyFixtureUnique"}, self.root)["items"], [])
        found = knowledge_search({"query": "LegacyFixtureUnique", "corpus": "legacy_malta"}, self.root)
        self.assertEqual(len(found["items"]), 1)
        with self.assertRaises(TaskError):
            knowledge_search({"query": "test", "corpus": "arbitrary_database"}, self.root)

    def test_incorrect_digest_count_or_schema_never_publishes_a_corpus(self):
        for setting in ("bad_digest", "wrong_count", "extra_field"):
            with self.subTest(setting=setting):
                self.configure(**{setting: True})
                result = restore(self.root)
                self.assertEqual(result["state"], "blocked")
                self.assertEqual(result["records"], 0)
                with self.assertRaises(TaskError):
                    search({"query": "LegacyFixtureUnique"}, self.root)


if __name__ == "__main__":
    unittest.main()
