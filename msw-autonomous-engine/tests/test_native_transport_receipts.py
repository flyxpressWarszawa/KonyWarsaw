from pathlib import Path
import unittest
from unittest.mock import patch

from msw_engine.native_autonomy import execute
from msw_engine.tasks import TaskError


class NativeTransportReceiptTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"schema": "msw.native-autonomy.job.v1", "execution_id": "a" * 64}
        self.receipt = {"schema": "msw.native-autonomy.receipt.v1", "state": "completed",
                        "execution_id": "a" * 64, "native_callbacks_executed": True,
                        "storage_readback_verified": True, "business_effect_verified": False}

    @patch("msw_engine.native_autonomy.time.sleep")
    def test_late_500_recovers_only_verified_receipt_with_same_identity(self, sleep):
        with patch("msw_engine.federation.gateway", side_effect=[
            TaskError("fed_autonomy_execute_http_500:internal_server_error"), self.receipt
        ]) as gateway:
            result = execute(self.payload, Path("."))
        self.assertTrue(result["receipt_transport_recovered"])
        self.assertTrue(result["storage_readback_verified"])
        self.assertFalse(result["business_effect_verified"])
        self.assertEqual(gateway.call_count, 2)
        self.assertEqual(gateway.call_args_list[0], gateway.call_args_list[1])

    @patch("msw_engine.native_autonomy.time.sleep")
    def test_repeated_failure_stops_after_three_attempts_and_never_fakes_success(self, sleep):
        with patch("msw_engine.federation.gateway", side_effect=TaskError("federation_unavailable")) as gateway:
            with self.assertRaisesRegex(TaskError, "federation_unavailable"):
                execute(self.payload, Path("."))
        self.assertEqual(gateway.call_count, 3)

    @patch("msw_engine.native_autonomy.time.sleep")
    def test_source_or_permission_rejection_is_not_retried(self, sleep):
        for code in ["fed_autonomy_execute_http_409:msw_autonomy_source_changed",
                     "fed_autonomy_execute_http_403:mswf_signature"]:
            with self.subTest(code=code), patch("msw_engine.federation.gateway", side_effect=TaskError(code)) as gateway:
                with self.assertRaises(TaskError):
                    execute(self.payload, Path("."))
                gateway.assert_called_once()

    @patch("msw_engine.native_autonomy.time.sleep")
    def test_recovered_transport_does_not_accept_unverified_receipt(self, sleep):
        with patch("msw_engine.federation.gateway", side_effect=[
            TaskError("federation_unavailable"), {**self.receipt, "storage_readback_verified": False}
        ]):
            with self.assertRaisesRegex(TaskError, "receipt_invalid"):
                execute(self.payload, Path("."))


if __name__ == "__main__":
    unittest.main()
