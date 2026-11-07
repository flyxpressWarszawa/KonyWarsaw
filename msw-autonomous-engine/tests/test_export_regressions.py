import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from msw_engine.chatgpt_export import import_zip


class ExportRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / 'export.zip'

    def write_export(self, parts):
        rows = [{'id': 'conversation-1', 'title': 'Line test', 'mapping': {
            'message-1': {'message': {'author': {'role': 'user'}, 'content': {'parts': parts}}}}}]
        with ZipFile(self.archive, 'w') as archive:
            archive.writestr('conversations.json', json.dumps(rows))

    def test_import_keeps_newlines_and_uses_nul_digest_separator(self):
        self.write_export(['first line', 'second line'])
        receipt = import_zip(self.archive, self.root)
        db_path = self.root / 'chatgpt-imports' / (receipt['archive_sha256'] + '.sqlite3')
        with sqlite3.connect(db_path) as db:
            title, body, digest = db.execute('SELECT title,body,digest FROM documents').fetchone()
        self.assertEqual(body, 'first line\nsecond line')
        self.assertEqual(digest, hashlib.sha256((title + '\0' + body).encode()).hexdigest())

    def test_non_text_parts_are_ignored_without_aborting_import(self):
        self.write_export(['usable text', 42, {'text': 'also usable'}, {'text': ['not', 'text']}])
        receipt = import_zip(self.archive, self.root)
        db_path = self.root / 'chatgpt-imports' / (receipt['archive_sha256'] + '.sqlite3')
        with sqlite3.connect(db_path) as db:
            body = db.execute('SELECT body FROM documents').fetchone()[0]
        self.assertEqual(body, 'usable text\nalso usable')


if __name__ == '__main__':
    unittest.main()
