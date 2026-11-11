import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from msw_engine.rag_training import RIGHTS, snapshot


class ApprovedTrainingSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.db = self.data / 'knowledge.sqlite3'
        with sqlite3.connect(self.db) as conn:
            conn.execute('CREATE TABLE documents (id TEXT PRIMARY KEY,title TEXT,body TEXT,url TEXT,digest TEXT,provenance TEXT)')
        self.put('public:training', 'MSW: prace techniczne', 'Bezpieczna diagnostyka urządzenia wymaga sprawdzenia instalacji oraz dokumentacji. ' * 8)
        self.put('public:holdout', 'MSW: walidacja', 'Technik analizuje zgłoszenie i przygotowuje zalecenia oparte na źródłach oraz pomiarach. ' * 4)

    def put(self, ident, title, body, provenance=RIGHTS+'operator-reviewed'):
        digest = hashlib.sha256((title + '\0' + body).encode()).hexdigest()
        with sqlite3.connect(self.db) as conn:
            conn.execute('INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?)', (ident, title, body, '', digest, provenance))

    def test_train_and_holdout_snapshot_pinned_and_reproducible(self):
        args = (self.data, ['public:training'], ['public:holdout'])
        first, second = snapshot(*args), snapshot(*args)
        self.assertEqual(first['sha256'], second['sha256'])
        self.assertEqual(first['train_records'], 1)
        self.assertEqual(first['validation_records'], 1)
        self.assertEqual(len(first['sha256']), 64)
        self.put('public:training', 'MSW: prace techniczne', 'Inna informacja techniczna wymagająca nowego snapshotu. ' * 11)
        self.assertNotEqual(first['sha256'], snapshot(*args)['sha256'])

    def test_rejects_owner_unreviewed_content(self):
        self.put('public:training', 'MSW', 'To źródło nie ma zatwierdzonych praw do treningu. ' * 8,
                 'owner_supplied_unverified')
        with self.assertRaisesRegex(ValueError, 'rights'):
            snapshot(self.data, ['public:training'], ['public:holdout'])

    def test_rejects_unsafe_sources(self):
        self.put('public:holdout', 'MSW', 'Kontakt +48 570 123 456. Szkolenia wymagają bezpiecznej anonimizacji. ' * 4)
        with self.assertRaisesRegex(ValueError, 'sensitive'):
            snapshot(self.data, ['public:training'], ['public:holdout'])

    def test_rejects_missing_ambiguous_and_malicious_ids(self):
        for training, holdout in [(['public:training'], ['public:training']),
                                  (['../private'], ['public:holdout']),
                                  (['public:training', 'public:training'], ['public:holdout']),
                                  (['missing'], ['public:holdout']),
                                  ([], ['public:holdout'])]:
            with self.assertRaises(ValueError):
                snapshot(self.data, training, holdout)

    def test_rejects_unpinned_database_mutation(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute('UPDATE documents SET body=? WHERE id=?', ('modified', 'public:holdout'))
        with self.assertRaisesRegex(ValueError, 'size|digest'):
            snapshot(self.data, ['public:training'], ['public:holdout'])

    def test_does_not_follow_database_symlinks(self):
        other = self.data / 'copy.sqlite3'
        self.db.rename(other)
        self.db.symlink_to(other)
        with self.assertRaisesRegex(ValueError, 'corpus_missing'):
            snapshot(self.data, ['public:training'], ['public:holdout'])


if __name__ == '__main__':
    unittest.main()
