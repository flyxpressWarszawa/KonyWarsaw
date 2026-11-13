"""Offline tests: GitHub public download never claims Hermes ingestion."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools import msw_hermes_github_pull as puller


class TestHermesPublicPull(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.path = "msw-autonomous-engine/knowledge/sample-public-doc.json"
        self.raw = b'{"public":true}\n'
        self.good_sha = hashlib.sha256(self.raw).hexdigest()
        self.row = {
            "path": self.path,
            "sha256": self.good_sha,
            "source_kind": "source_only_document",
            "raw_url": puller.PREFIX + self.path,
        }
        self.base = {
            "schema": "msw.hermes.github-auto-index.v1",
            "repository": puller.REPO,
            "branch": puller.BRANCH,
            "source_state": "GITHUB_ARCHIVED_NOT_HERMES_INGESTED",
            "unique_public_source_urls": 0,
            "source_items": [self.row],
        }
        self.calls = []

    def fake(self, url, limit):
        self.calls.append(url)
        if url == puller.INDEX_URL:
            return json.dumps(self.base).encode()
        if url == puller.PREFIX + self.path:
            return self.raw
        raise RuntimeError("not_allowlisted")

    def test_verified_public_download_and_receipt(self):
        result = puller.pull(self.directory, fetch=self.fake)
        self.assertEqual(result["files"], 1)
        self.assertEqual(result["status"], "PUBLIC_DOWNLOAD_VERIFIED_NOT_HERMES_INGESTED")
        self.assertEqual((self.directory / self.path).read_bytes(), self.raw)
        receipt = json.loads((self.directory / "HERMES-GITHUB-DOWNLOAD-RECEIPT.json").read_text())
        self.assertFalse(receipt["receipts"][0]["ingested_into_hermes"])

    def test_second_pull_uses_verified_cache(self):
        puller.pull(self.directory, fetch=self.fake)
        self.calls.clear()
        puller.pull(self.directory, fetch=self.fake)
        self.assertEqual(self.calls, [puller.INDEX_URL])

    def test_digest_mismatch_never_writes(self):
        self.base["source_items"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "source_digest_mismatch"):
            puller.pull(self.directory, fetch=self.fake)
        self.assertFalse((self.directory / self.path).exists())

    def test_unexpected_manifest_identity_fails_closed(self):
        self.base["branch"] = "main"
        with self.assertRaisesRegex(ValueError, "manifest_identity_mismatch"):
            puller.pull(self.directory, fetch=self.fake)

    def test_manifest_mismatched_url_fails(self):
        self.base["source_items"][0]["raw_url"] = "https://example.org/file"
        with self.assertRaisesRegex(ValueError, "manifest_url_mismatch"):
            puller.pull(self.directory, fetch=self.fake)

    def test_duplicate_record_is_denied(self):
        self.base["source_items"].append(dict(self.row))
        with self.assertRaisesRegex(ValueError, "duplicate_manifest_path"):
            puller.pull(self.directory, fetch=self.fake)

    def test_path_traversal_denied(self):
        self.assertRaisesRegex(ValueError, "path_traversal", puller._safe_path, "msw-autonomous-engine/knowledge/../private.txt")

    def test_code_candidate_is_not_downloaded_by_default(self):
        self.base["source_items"] = [{"source_kind": "code_candidate", "path": "msw-autonomous-engine/msw_engine/unsafe.py"}]
        out = puller.pull(self.directory, fetch=self.fake)
        self.assertEqual(out["files"], 0)

    def test_symlink_destination_is_denied(self):
        target = self.directory / self.path
        target.parent.mkdir(parents=True)
        target.symlink_to(self.directory / "unrelated")
        with self.assertRaisesRegex(ValueError, "symlink_destination_rejected"):
            puller.pull(self.directory, fetch=self.fake)


if __name__ == "__main__":
    unittest.main()
