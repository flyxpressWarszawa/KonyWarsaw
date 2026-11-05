from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from msw_engine.native_autonomy import execute
from msw_engine.tasks import TaskError, handlers


class NativeAutonomyTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"schema": "msw.native-autonomy.job.v1", "execution_id": "a"*64}
        self.receipt = {"schema": "msw.native-autonomy.receipt.v1", "state": "completed", "execution_id": "a"*64,
                        "native_callbacks_executed": True, "storage_readback_verified": True, "business_effect_verified": False}

    def test_registered_typed_native_handler(self):
        with patch.dict("os.environ", {"MSW_RUNTIME_MODE": "vps", "MSW_WP_URL": "https://example.test", "MSW_NODE": "test-node", "MSW_SECRET": "s"*32}):
            self.assertIn("module.execute", handlers())

    def test_native_receipt_required(self):
        with patch("msw_engine.federation.gateway", return_value=self.receipt) as gateway:
            self.assertEqual(execute(self.payload, Path(".")), self.receipt)
            gateway.assert_called_once_with("autonomy_execute", {"execution_id": "a"*64})

    def test_unknown_hook_code_or_arguments_cannot_be_sent(self):
        for extra in [{"hook": "delete_everything"}, {"code": "eval()"}, {"args": ["customer-data"]}]:
            with self.subTest(extra=extra), patch("msw_engine.federation.gateway") as gateway:
                with self.assertRaises(TaskError):
                    execute({**self.payload, **extra}, Path("."))
                gateway.assert_not_called()

    def test_running_native_task_is_polled_without_new_execution_identity(self):
        with patch("msw_engine.federation.gateway", side_effect=[{"state": "running_or_uncertain"}, self.receipt]) as gateway, patch("msw_engine.native_autonomy.time.sleep"):
            result = execute(self.payload, Path("."))
        self.assertTrue(result["native_callbacks_executed"])
        self.assertEqual(gateway.call_args_list[0], gateway.call_args_list[1])

    def test_unverified_or_different_native_receipt_is_rejected(self):
        for replacement in [{"storage_readback_verified": False}, {"execution_id": "b"*64}, {"native_callbacks_executed": False}]:
            with self.subTest(replacement=replacement), patch("msw_engine.federation.gateway", return_value={**self.receipt, **replacement}):
                with self.assertRaisesRegex(TaskError, "receipt_invalid"):
                    execute(self.payload, Path("."))

    def test_transport_failure_never_becomes_a_static_success(self):
        with patch("msw_engine.federation.gateway", side_effect=TaskError("federation_unavailable")):
            with self.assertRaises(TaskError):
                execute(self.payload, Path("."))

    def test_satellite_receipt_is_bound_to_actual_site_and_hmac_readback(self):
        site = "733bcd1b-a160-419b-8b2c-531811c2b6a0"
        payload = {**self.payload, "site_id": site}
        receipt = {**self.receipt, "site_id": site, "satellite_hmac_readback_verified": True}
        with patch("msw_engine.federation.gateway", return_value=receipt) as gateway:
            self.assertEqual(execute(payload, Path(".")), receipt)
            gateway.assert_called_once_with("autonomy_execute", {"execution_id": "a"*64, "site_id": site})
        for change in [{"site_id": "cb12cbaf-be8e-4679-b34f-cf0471d2d266"}, {"satellite_hmac_readback_verified": False}]:
            with self.subTest(change=change), patch("msw_engine.federation.gateway", return_value={**receipt, **change}):
                with self.assertRaisesRegex(TaskError, "receipt_invalid"):
                    execute(payload, Path("."))

    def test_satellite_address_or_hash_cannot_replace_actual_uuid(self):
        for site in ["https://example.test", "a"*64, "../another", None]:
            with self.subTest(site=site), patch("msw_engine.federation.gateway") as gateway:
                with self.assertRaisesRegex(TaskError, "site_id"):
                    execute({**self.payload, "site_id": site}, Path("."))
                gateway.assert_not_called()


if __name__ == "__main__":
    unittest.main()
