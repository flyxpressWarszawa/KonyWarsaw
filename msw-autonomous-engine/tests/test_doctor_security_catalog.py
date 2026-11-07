import json
from pathlib import Path
import tempfile
import unittest

from msw_engine.doctor_security_catalog import CATALOG, import_catalog, load_records, search_catalog


class SecurityCatalogTests(unittest.TestCase):
    def test_catalog_import_has_verified_readback_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = import_catalog(tmp)
            self.assertEqual(first['records'], 895)
            self.assertEqual(first['readback_verified_records'], 895)
            self.assertEqual(first['plugins_installed'], 0)
            self.assertFalse(first['training_performed'])
            second = import_catalog(tmp)
            self.assertEqual(second['unchanged'], 895)
            hit = search_catalog(tmp, 'Wordfence', limit=5)
            self.assertTrue(hit['items'])
            self.assertTrue(all(x['classification'] == 'unverified_public_research' for x in hit['items']))
            self.assertEqual(hit['classification'], 'unverified_public_research')

    def test_rejects_untrusted_catalog_url_before_any_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = json.loads(CATALOG.read_text(encoding='utf-8'))
            body['plugins'][0]['url'] = 'https://evil.example/plugin.zip'
            path = Path(tmp) / 'bad.json'
            path.write_text(json.dumps(body), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'catalog_url_invalid'):
                import_catalog(tmp, path=path)
            self.assertFalse((Path(tmp) / 'doctor-security-research').exists())

    def test_deprecated_entries_are_not_disguised_as_safe(self):
        records = load_records()
        vault = next(x for x in records if x['id'] == 'doctor:security:plugin:vaultpress')
        self.assertIn('Deprecated notice found', vault['text'])
        self.assertIn('UNVERIFIED', vault['text'])
