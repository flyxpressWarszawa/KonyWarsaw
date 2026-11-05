import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from msw_engine.catalog_publisher import CatalogPublisher, CatalogError


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cursor, self.verified, self.events = 17, 0, []
        self.lost_response = False
        self.errors = 0

    def tearDown(self):
        self.tmp.cleanup()

    def remote(self, operation, **values):
        self.events.append((operation, values))
        if operation == 'catalog':
            self.assertEqual(values['expected_cursor'], self.cursor)
            self.cursor += 1
            if self.lost_response:
                self.lost_response = False
                raise CatalogError('catalog_transport_unavailable')
        if operation == 'assets':
            self.assertEqual(values['index'], self.verified)
            self.verified += 1
        return {'catalog': {'state': 'running', 'mode': 'apply', 'cursor': self.cursor, 'errors': self.errors},
                'assets': {'manifest_sha': 'a' * 64, 'verified': self.verified,
                           'total': 2, 'pending_indices': list(range(self.verified, 2))}}

    def publisher(self):
        return CatalogPublisher(SimpleNamespace(wordpress_url='https://mobilny-serwis-warszawa.pl'), Path(self.tmp.name), call=self.remote)

    def test_alternates_catalog_and_assets_with_readback(self):
        pub = self.publisher()
        self.assertEqual(pub.step()['images_verified'], 1)
        self.assertEqual(pub.step()['catalog_cursor'], 18)
        self.assertEqual([e[0] for e in self.events], ['status', 'assets', 'status', 'status', 'catalog', 'status'])

    def test_uncertain_commit_is_read_before_next_write(self):
        pub = self.publisher()
        self.verified = 2
        self.lost_response = True
        with self.assertRaises(CatalogError):
            pub.step()
        pub.step()
        writes = [v['expected_cursor'] for op, v in self.events if op == 'catalog']
        self.assertEqual(writes, [17, 18])

    def test_existing_import_errors_do_not_continue_products(self):
        self.errors, self.verified = 1, 2
        self.assertEqual(self.publisher().step()['action'], 'observe')
        self.assertFalse(any(op == 'catalog' for op, _ in self.events))

    def test_paused_catalog_does_not_restart_itself(self):
        pub = self.publisher()
        pub.call = lambda *a, **k: {'catalog': {'state': 'paused', 'mode': 'apply', 'cursor': 17, 'errors': 0}, 'assets': {}}
        self.assertEqual(pub.step()['action'], 'observe')

    def test_receipt_survives_process_restart(self):
        self.publisher().step()
        restarted = self.publisher()
        with restarted.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0], 1)
        self.assertTrue((restarted.directory / 'status.json').is_file())

    def test_rejects_other_site_and_credential_urls(self):
        for url in ['https://other.example', 'http://mobilny-serwis-warszawa.pl', 'https://user@mobilny-serwis-warszawa.pl', 'https://mobilny-serwis-warszawa.pl/x']:
            with self.assertRaises(ValueError):
                CatalogPublisher(SimpleNamespace(wordpress_url=url), Path(self.tmp.name))

    def test_process_lock_prevents_concurrent_publishers(self):
        import fcntl
        pub = self.publisher()
        with (pub.directory / 'publisher.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(pub.step(), {'state': 'busy'})
        self.assertEqual(self.events, [])


if __name__ == '__main__':
    unittest.main()
