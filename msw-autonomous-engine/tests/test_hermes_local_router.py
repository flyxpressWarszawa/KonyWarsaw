"""Offline tests: no provider calls, no WordPress writes, no model charges."""
from __future__ import annotations
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from msw_engine.hermes_local_router import (
    RouterError, admit, execute_once, generate, _verify_response,
    send_local, MODEL, ENDPOINT,
)

def sample(**changes):
    p = {"prompt": "Przetestuj polaczenie.", "task": "technical",
         "max_tokens": 128, "request_id": "msw-native-local-0001",
         "collaborate": False, "use_knowledge": False}
    p.update(changes)
    return p


def model_response(text="Potwierdzam.", finish="stop", model="qwen3-0.6b"):
    return {"id": "chatcmpl-offline", "model": model,
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", "content": text}}]}


def verified_result():
    return _verify_response(model_response())


class TestHermesLocalRouter(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "router"
        self.calls = []
        self.result = verified_result()

    def tearDown(self):
        self.temp.cleanup()

    def sender(self, task, prompt, limit, request_id):
        self.calls.append(request_id)
        return dict(self.result)

    def test_valid_contract(self):
        self.assertEqual(admit(sample()), ("technical", "Przetestuj polaczenie.", 128, "msw-native-local-0001"))

    def test_unknown_fields_block(self):
        with self.assertRaisesRegex(RouterError, "invalid_job_contract"):
            admit(sample(productionMutationAllowed=True))

    def test_request_id_required(self):
        for request_id in ("", "short", "../path", None, "!"*15):
            with self.subTest(request_id=request_id), self.assertRaises(RouterError):
                admit(sample(request_id=request_id))

    def test_prompt_limit(self):
        for s in ("", "   ", "z" * 6001):
            with self.subTest(prompt=s[:6]), self.assertRaises(RouterError):
                admit(sample(prompt=s))

    def test_contact_data_rejected(self):
        for s in ("admin@example.org", "555 444 333", "570933114"):
            with self.subTest(s=s), self.assertRaisesRegex(RouterError, "customer_data_rejected"):
                admit(sample(prompt=s))

    def test_model_options_and_types(self):
        for value in (True, 2.7, -1, 0, 1025, "300"):
            with self.subTest(value=value), self.assertRaisesRegex(RouterError, "token_limit"):
                admit(sample(max_tokens=value))

    def test_forbid_misreported_collaboration(self):
        for field in ("collaborate", "use_knowledge"):
            for value in (True, "false"):
                with self.subTest(field=field, value=value), self.assertRaises(RouterError):
                    admit(sample(**{field: value}))

    def test_task_allowlist(self):
        with self.assertRaisesRegex(RouterError, "task_forbidden"):
            admit(sample(task="delete_wordpress"))

    def test_verified_answer_hash(self):
        out = self.result
        self.assertEqual(out["provider"], "hermes-local-router")
        self.assertEqual(out["requestModel"], MODEL)
        self.assertTrue(out["router_response_verified"])
        self.assertEqual(hashlib.sha256(out["text"].encode()).hexdigest(), out["answer_sha256"])
        self.assertFalse(out["publication_performed"])

    def test_incomplete_model_result(self):
        for stop in ("length", "tool_calls", "content_filter", None):
            with self.subTest(stop=stop), self.assertRaisesRegex(RouterError, "incomplete"):
                _verify_response(model_response(finish=stop))

    def test_empty_model_result(self):
        with self.assertRaisesRegex(RouterError, "model_reply_empty"):
            _verify_response(model_response(text=" "))

    def test_no_tools_called(self):
        z = model_response()
        z["choices"][0]["message"]["tool_calls"] = [{"function": {"name": "do_something"}}]
        with self.assertRaisesRegex(RouterError, "unexpected_tool_call"):
            _verify_response(z)

    def test_invalid_provider_reply(self):
        for x in ({}, [], {"choices": [{"message": {"content": "x"}}]}):
            with self.subTest(x=x), self.assertRaises(RouterError):
                _verify_response(x)

    def test_one_success_and_receipt_readback(self):
        first = execute_once(sample(), self.root, self.sender)
        second = execute_once(sample(), self.root, self.sender)
        self.assertEqual(first, second)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual((self.root.stat().st_mode & 0o777), 0o700)
        self.assertEqual(((self.root/"requests.sqlite3").stat().st_mode & 0o777), 0o600)
        with closing(sqlite3.connect(self.root/"requests.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT state FROM requests").fetchone()[0], "completed")

    def test_conflict_same_id_new_payload(self):
        execute_once(sample(), self.root, self.sender)
        with self.assertRaisesRegex(RouterError, "request_id_conflict"):
            execute_once(sample(prompt="Nowe polecenie."), self.root, self.sender)
        self.assertEqual(len(self.calls), 1)

    def test_unknown_response_never_reissued(self):
        def fails(*_):
            self.calls.append("inflight")
            raise TimeoutError("unknown")
        with self.assertRaisesRegex(RouterError, "unknown_outcome_no_reissue"):
            execute_once(sample(), self.root, fails)
        with self.assertRaisesRegex(RouterError, "unknown_outcome_no_reissue"):
            execute_once(sample(), self.root, self.sender)
        self.assertEqual(self.calls, ["inflight"])
        with closing(sqlite3.connect(self.root/"requests.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT state FROM requests").fetchone()[0], "pending")

    def test_invalid_sender_result_never_committed(self):
        def bad(*_):
            self.calls.append("invalid")
            return {"text": "not a verified result"}
        with self.assertRaisesRegex(RouterError, "unverified_model_result"):
            execute_once(sample(), self.root, bad)
        with self.assertRaisesRegex(RouterError, "unknown_outcome_no_reissue"):
            execute_once(sample(), self.root, self.sender)
        self.assertEqual(self.calls, ["invalid"])

    def test_receipt_tamper_fail_closed(self):
        execute_once(sample(), self.root, self.sender)
        with closing(sqlite3.connect(self.root/"requests.sqlite3")) as db:
            db.execute("UPDATE requests SET result_json='{}'")
            db.commit()
        with self.assertRaisesRegex(RouterError, "receipt_integrity"):
            execute_once(sample(), self.root, self.sender)
        self.assertEqual(len(self.calls), 1)

    def test_symlink_directory_rejected(self):
        target = Path(self.temp.name)/"target"
        target.mkdir()
        self.root.symlink_to(target)
        with self.assertRaisesRegex(RouterError, "storage_symlink"):
            execute_once(sample(), self.root, self.sender)
        self.assertEqual(len(self.calls), 0)

    def test_symlink_database_rejected(self):
        self.root.mkdir()
        (self.root/"requests.sqlite3").symlink_to(Path(self.temp.name)/"other")
        with self.assertRaisesRegex(RouterError, "storage_symlink"):
            execute_once(sample(), self.root, self.sender)
        self.assertEqual(len(self.calls), 0)

    def test_deny_disabled_flag(self):
        with patch.dict(os.environ, {}, clear=True):
            from msw_engine.tasks import TaskError
            with self.assertRaisesRegex(TaskError, "local_router_disabled"):
                generate(sample(), self.root)

    def test_deny_wrong_worker(self):
        with patch.dict(os.environ, {"MSW_HERMES_LOCAL_ROUTER_ENABLED": "1",
                                     "MSW_NODE": "staging"}, clear=True):
            from msw_engine.tasks import TaskError
            with self.assertRaisesRegex(TaskError, "wrong_worker_identity"):
                generate(sample(), self.root)

    def test_deny_ephemeral_disk(self):
        with patch.dict(os.environ, {"MSW_HERMES_LOCAL_ROUTER_ENABLED": "1",
                                     "MSW_NODE": "msw-render-central-1"}, clear=True):
            from msw_engine.tasks import TaskError
            with patch("msw_engine.hermes_local_router.os.path.ismount", return_value=False):
                with self.assertRaisesRegex(TaskError, "persistent_disk_not_verified"):
                    generate(sample(), self.root)

    def test_fixed_local_endpoint_no_auto_retry(self):
        class Reply:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _n): return json.dumps(model_response()).encode()
        class Opener:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return Reply()
        opener = Opener()
        with patch("msw_engine.hermes_local_router.build_opener", return_value=opener):
            out = send_local("technical", "Opis testowy.", 128, "msw-native-local-0001")
        self.assertEqual(out["provider"], "hermes-local-router")
        self.assertEqual(opener.request.full_url, ENDPOINT)
        self.assertEqual(opener.timeout, 115)
        self.assertEqual(json.loads(opener.request.data)["model"], "msw-auto")
        self.assertNotIn(b"sk-proj", opener.request.data)

    def test_optin_dispatches_native_without_fallback(self):
        from msw_engine import tasks
        data_dir = Path(self.temp.name)
        with patch.dict(os.environ, {"MSW_HERMES_LOCAL_ROUTER_ENABLED": "1",
                                     "MSW_NODE": "msw-render-central-1"}, clear=True):
            with patch("msw_engine.hermes_local_router.generate",
                       return_value={"channel": "localhost"}) as native, \
                 patch("msw_engine.federation.generate") as legacy:
                out = tasks.llm_dispatch(sample(), data_dir)
                self.assertEqual(out, {"channel": "localhost"})
                native.assert_called_once()
                legacy.assert_not_called()

    def test_unknown_native_never_falls_back_to_paid_wordpress(self):
        from msw_engine import tasks
        with patch.dict(os.environ, {"MSW_HERMES_LOCAL_ROUTER_ENABLED": "1",
                                     "MSW_NODE": "msw-render-central-1"}, clear=True):
            with patch("msw_engine.hermes_local_router.generate",
                       side_effect=tasks.TaskError("unknown_outcome_no_reissue")) as native, \
                 patch("msw_engine.federation.generate") as legacy:
                with self.assertRaisesRegex(tasks.TaskError, "unknown_outcome_no_reissue"):
                    tasks.llm_dispatch(sample(), Path(self.temp.name))
                native.assert_called_once()
                legacy.assert_not_called()

    def test_owner_model_contract_precedes_native_router(self):
        from msw_engine import tasks
        with patch.dict(os.environ, {"MSW_HERMES_LOCAL_ROUTER_ENABLED": "1"}, clear=True):
            with patch("msw_engine.own_model.handle",
                       return_value={"branch": "owner"}) as original, \
                 patch("msw_engine.hermes_local_router.generate") as local:
                self.assertEqual(tasks.llm_dispatch({"schema": "msw.own-model.job.v1"},
                                                     Path(self.temp.name)), {"branch": "owner"})
                original.assert_called_once()
                local.assert_not_called()

    def test_alibaba_queue_contract_precedes_native_router(self):
        from msw_engine import tasks
        from msw_engine.aliyun_direct import QUEUE_SCHEMA
        with patch.dict(os.environ, {"MSW_HERMES_LOCAL_ROUTER_ENABLED": "1"}, clear=True):
            with patch("msw_engine.aliyun_direct.queue_generate",
                       return_value={"branch": "aliyun"}) as aliyun, \
                 patch("msw_engine.hermes_local_router.generate") as local:
                self.assertEqual(tasks.llm_dispatch({"schema": QUEUE_SCHEMA},
                                                     Path(self.temp.name)), {"branch": "aliyun"})
                aliyun.assert_called_once()
                local.assert_not_called()

    def test_legacy_route_unchanged_without_optin(self):
        from msw_engine import tasks
        with patch.dict(os.environ, {"MSW_LLM_MODEL": "gpt-5-mini",
                                     "MSW_LLM_PROVIDER": "wordpress"}, clear=True):
            with patch("msw_engine.federation.configured", return_value=True), \
                 patch("msw_engine.federation.generate",
                       return_value={"branch": "legacy"}) as legacy, \
                 patch("msw_engine.hermes_local_router.generate") as local:
                self.assertEqual(tasks.llm_dispatch(sample(), Path(self.temp.name)),
                                 {"branch": "legacy"})
                legacy.assert_called_once()
                local.assert_not_called()

    def test_optin_declares_native_handler(self):
        from msw_engine import tasks
        with patch.dict(os.environ, {"MSW_HERMES_LOCAL_ROUTER_ENABLED": "1"}, clear=True):
            self.assertIn("llm.generate", tasks.handlers())

    def test_no_external_provider_key_needed(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_verify_response(model_response())["requestModel"], "msw-auto")


if __name__ == "__main__":
    unittest.main()
