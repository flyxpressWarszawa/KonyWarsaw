import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from msw_engine.module_bus import handle, _persist, SCHEMA
from msw_engine.tasks import TaskError, runtime_catalog


HASH = "a" * 64


def module(number=0, active=True, loaded=True):
    return {"id": f"msw-{number}/main.php", "name": "MSW", "version": "1.0", "active": active,
            "loaded": loaded, "source_sha256": "b" * 64, "native_probe": "neural.status" if active else None}


class ModuleBusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def sync(self, rows=None):
        rows = rows if rows is not None else [module()]
        def gateway(operation, args):
            self.assertEqual(operation, "modules_catalog")
            offset = args["offset"]
            batch = rows[offset:offset+40]
            nxt = offset + len(batch)
            return {"schema": SCHEMA, "catalog_sha256": HASH, "total": len(rows), "offset": offset,
                    "modules": batch, "next_offset": nxt if nxt < len(rows) else None}
        with patch("msw_engine.federation.gateway", side_effect=gateway):
            return handle({"module_bus": {"operation": "sync", "catalog_sha256": HASH}}, self.path)

    def test_all_291_plugins_persisted_and_independently_read_back(self):
        result = self.sync([module(i, active=i < 276) for i in range(291)])
        self.assertEqual(result["modules_total"], 291)
        self.assertEqual(result["active"], 276)
        self.assertEqual(result["states"], {"local_runtime_observed": 276, "installed_inactive": 15})
        self.assertTrue(result["storage_readback_verified"])
        self.assertFalse(result["business_execution_verified"])
        with sqlite3.connect(self.path / "module-bus.sqlite3") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM modules").fetchone()[0], 291)

    def test_removed_plugin_does_not_remain_active_in_new_snapshot(self):
        self.sync([module(0), module(1)])
        self.sync([module(0)])
        with sqlite3.connect(self.path / "module-bus.sqlite3") as db:
            self.assertEqual(db.execute("SELECT id FROM modules").fetchall(), [("msw-0/main.php",)])

    def test_pagination_rejects_drift_instead_of_partial_success(self):
        with patch("msw_engine.federation.gateway", return_value={"schema": SCHEMA, "catalog_sha256": "c"*64}):
            with self.assertRaisesRegex(TaskError, "catalog_changed"):
                handle({"module_bus": {"operation": "sync", "catalog_sha256": HASH}}, self.path)

    def test_empty_middle_page_rejected(self):
        page = {"schema": SCHEMA, "catalog_sha256": HASH, "total": 10, "offset": 0, "modules": [], "next_offset": 0}
        with patch("msw_engine.federation.gateway", return_value=page):
            with self.assertRaisesRegex(TaskError, "page_contract"):
                handle({"module_bus": {"operation": "sync", "catalog_sha256": HASH}}, self.path)

    def test_duplicate_plugin_rejected(self):
        with self.assertRaisesRegex(TaskError, "duplicate_module"):
            self.sync([module(), module()])

    def test_no_arbitrary_route_or_code_can_be_forwarded(self):
        for extra in ({"route": "/wp/v2/users"}, {"code": "eval()"}, {"operation": "execute"}):
            args = {"operation": "sync", "catalog_sha256": HASH, **extra}
            with self.assertRaises(TaskError):
                handle({"module_bus": args}, self.path)

    def test_probes_require_previously_synchronized_identity(self):
        with self.assertRaisesRegex(TaskError, "probe_not_synchronized"):
            handle({"module_bus": {"operation": "probe", "catalog_sha256": HASH, "ids": ["msw-0/main.php"]}}, self.path)

    def test_probe_saves_native_receipt_without_claiming_business_effect(self):
        self.sync()
        row = {"id": "msw-0/main.php", "native_read_executed": True, "native_result_sha256": "d"*64, "business_execution_verified": False}
        with patch("msw_engine.federation.gateway", return_value=row):
            result = runtime_catalog({"module_bus": {"operation": "probe", "catalog_sha256": HASH, "ids": [row["id"]]}}, self.path)
        self.assertTrue(result["storage_readback_verified"])
        self.assertTrue(result["modules"][0]["native_read_executed"])
        self.assertFalse(result["business_execution_verified"])

    def test_network_failure_is_per_module_failure_not_fake_native_success(self):
        self.sync()
        with patch("msw_engine.federation.gateway", side_effect=TaskError("federation_unavailable")):
            result = handle({"module_bus": {"operation": "probe", "catalog_sha256": HASH, "ids": ["msw-0/main.php"]}}, self.path)
        self.assertFalse(result["modules"][0]["native_read_executed"])
        self.assertEqual(result["modules"][0]["native_error"], "federation_unavailable")

    def test_active_without_loaded_runtime_remains_a_problem(self):
        self.assertEqual(self.sync([module(loaded=False)])["states"], {"active_not_loaded": 1})

    def test_path_traversal_refused(self):
        row = module()
        row["id"] = "../wp-config.php"
        with self.assertRaisesRegex(TaskError, "module_contract"):
            self.sync([row])

    def test_symlinked_database_refused(self):
        target = self.path / "other.sqlite3"
        target.touch()
        (self.path / "module-bus.sqlite3").symlink_to(target)
        with self.assertRaisesRegex(TaskError, "storage_link"):
            self.sync()


if __name__ == "__main__":
    unittest.main()
