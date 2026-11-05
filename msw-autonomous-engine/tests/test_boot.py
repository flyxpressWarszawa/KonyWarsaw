import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from msw_engine import __main__ as entry


class BootTest(unittest.TestCase):
    def test_production_site_reaches_worker_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = MagicMock()
            worker.loop.return_value = 0
            with patch.dict(os.environ, {'MSW_DATA_DIR': directory, 'MSW_WP_URL': 'https://mobilny-serwis-warszawa.pl'}), \
                 patch('sys.argv', ['msw_engine']), \
                 patch.object(entry, 'install', return_value={'state':'test', 'modules':[]}), \
                 patch('msw_engine.own_model.install', return_value={'state':'test'}), \
                 patch('msw_engine.business_hunter.checkpoint', return_value={'state':'test'}), \
                 patch('msw_engine.legacy_memory.restore', return_value={'state':'test'}), \
                 patch.object(entry, 'Client'), patch.object(entry, 'Worker', return_value=worker):
                self.assertEqual(entry.main(), 0)
                worker.loop.assert_called_once_with(once=False, poke_cron_seconds=300)


if __name__ == '__main__':
    unittest.main()
