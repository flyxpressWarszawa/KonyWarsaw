from pathlib import Path
import tempfile
import unittest

from msw_engine.tasks import knowledge_ingest, knowledge_search, TaskError


class EmptySourceTests(unittest.TestCase):
    def test_approved_empty_page_is_reported_and_never_enters_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = knowledge_ingest({"records": [
                {"id": "post:1", "title": "Empty public page", "text": "  ", "approved": True},
                {"id": "post:2", "title": "Useful public page", "text": "Verified maintenance procedure", "approved": True},
            ]}, root)
            self.assertEqual(result["stored"], 1)
            self.assertEqual(result["records_total"], 1)
            self.assertEqual(result["skipped_empty"], [{"id": "post:1", "reason": "empty_source_body"}])
            self.assertEqual(knowledge_search({"query": "Empty"}, root)["items"], [])

    def test_empty_body_does_not_bypass_approval_or_identity_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for record in ({"id": "post:1", "text": "", "approved": False},
                           {"id": "../unapproved", "text": "", "approved": True}):
                with self.assertRaises(TaskError):
                    knowledge_ingest({"records": [record]}, root)


if __name__ == "__main__":
    unittest.main()
