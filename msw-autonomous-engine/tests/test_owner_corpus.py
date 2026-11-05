import hashlib
import tempfile
import unittest
from pathlib import Path
from msw_engine.tasks import OWNER_CORPUS, TaskError, knowledge_ingest, knowledge_search


class OwnerCorpusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        text = 'Własny trenowany model MSW wymaga checkpointów i pomiaru jakości.'
        self.row = {'id': 'owner:abc', 'title': 'V7 model', 'text': text, 'approved': False,
                    'classification': 'owner_supplied_unverified', 'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                    'provenance': 'V7-owner-requirements.json SHA256 source'}

    def payload(self):
        return {'corpus': OWNER_CORPUS, 'owner_authorized': True, 'records': [self.row]}

    def test_owner_dedup_readback_and_search(self):
        ack = knowledge_ingest(self.payload(), self.root)
        self.assertEqual((ack['accepted_records'], ack['readback_verified_records'], ack['records_total']), (1, 1, 1))
        self.assertEqual(knowledge_ingest(self.payload(), self.root)['unchanged'], 1)
        result = knowledge_search({'corpus': OWNER_CORPUS, 'owner_authorized': True, 'query': 'trenowany model', 'match': 'all'}, self.root)
        self.assertEqual(result['items'][0]['classification'], 'owner_supplied_unverified')
        self.assertIn('V7-owner', result['items'][0]['provenance'])
        self.assertFalse(result['training_performed'])

    def test_default_corpus_isolation(self):
        knowledge_ingest(self.payload(), self.root)
        self.assertEqual(knowledge_search({'query': 'MSW'}, self.root)['items'], [])
        with self.assertRaisesRegex(TaskError, 'record_not_approved'):
            knowledge_ingest({'records': [self.row]}, self.root)
        approved = dict(self.row, id='approved:one', approved=True)
        knowledge_ingest({'records': [approved]}, self.root)
        self.assertEqual(knowledge_search({'query': 'MSW'}, self.root)['items'][0]['id'], 'approved:one')
        self.assertEqual(knowledge_search({'corpus': OWNER_CORPUS, 'owner_authorized': True, 'query': 'MSW'}, self.root)['records_total'], 1)

    def test_wrong_permissions_classification_hash_and_path(self):
        for mutation in ({'owner_authorized': False}, {'corpus': '../../arbitrary'}):
            with self.assertRaises(TaskError):knowledge_ingest(dict(self.payload(), **mutation), self.root)
        for mutation in ({'approved': True}, {'classification': 'verified'}, {'text_sha256': '0'*64}):
            with self.assertRaises(TaskError):knowledge_ingest(dict(self.payload(), records=[dict(self.row, **mutation)]), self.root)
        with self.assertRaises(TaskError):knowledge_search({'corpus': OWNER_CORPUS, 'query': 'MSW'}, self.root)

    def test_all_records_rejected_before_any_write(self):
        with self.assertRaises(TaskError):knowledge_ingest(dict(self.payload(), records=[self.row, dict(self.row, id='invalid/id')]), self.root)
        result = knowledge_search({'corpus': OWNER_CORPUS, 'owner_authorized': True, 'query': 'MSW'}, self.root)
        self.assertEqual(result['records_total'], 0)


if __name__ == '__main__':unittest.main()
