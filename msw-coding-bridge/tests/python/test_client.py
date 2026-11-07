import base64
from contextlib import redirect_stdout
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib import error, parse

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("coding_client", ROOT / "client.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

A, B = "a" * 40, "b" * 40
LEASE = "d" * 64
JWT = "eyJhbGciOiJSUzI1NiJ9." + "x" * 80 + ".signature"


def seal(value):
    value = copy.deepcopy(value)
    value["id"] = hashlib.sha256((value["source_engine"] + ":" + value["request_id"]).encode()).hexdigest()[:32]
    value.pop("request_sha256", None)
    value["request_sha256"] = hashlib.sha256(c.canonical(value)).hexdigest()
    return value


def task():
    return seal({"schema": c.TASK_SCHEMA, "request_id": "test-public-20261107", "source_engine": "chatgpt",
                 "target": "technical-core", "title": "Public test request", "objective": "Verify this test contract.",
                 "acceptance": ["Readback is independently verified."], "public_data": True,
                 "created_at": "2026-11-07T00:00:00Z"})


def environment():
    return {"GITHUB_REPOSITORY": c.REPOSITORY, "GITHUB_REF": "refs/heads/main",
            "GITHUB_WORKFLOW_REF": c.WORKFLOW_REF, "GITHUB_RUN_ID": "123456",
            "GITHUB_TOKEN": "test-github-token-never-log", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "test-request-token-never-log",
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://pipelines.actions.githubusercontent.com/test?audience=wrong&api-version=2"}


class FakeHTTP:
    """A synthetic API pair. No network, WordPress or production credentials."""

    def __init__(self):
        self.task = task()
        self.calls = []
        self.probe_state, self.claim_state = "connected", "leased"
        self.lease, self.expiry = LEASE, "2099-01-01T00:00:00Z"
        self.support = c.SUPPORT[self.task["target"]]
        self.heads, self.head_index = [A, B], 0
        self.files = {}
        self.write_error = None
        self.concurrent_content = None
        self.tampered_content = None
        self.ack_failures, self.ack_override = [], {}
        self.run_override = {}

    def request(self, method, url, token, payload=None, *, stage, **kwargs):
        self.calls.append({"method": method, "url": url, "token": token, "payload": copy.deepcopy(payload), "stage": stage, **kwargs})
        if stage == "oidc":
            return 200, {"value": JWT}
        if stage.startswith("exchange_"):
            op = payload["operation"]
            envelope = {"schema": c.EXCHANGE_SCHEMA, "ok": True, "run_id": self.run_override.get(op, "123456")}
            if op == "probe":
                return 200, {**envelope, "state": self.probe_state}
            if op == "claim":
                return 200, {**envelope, "state": self.claim_state, "task": self.task, "lease_token": self.lease,
                             "lease_expires_at": self.expiry, "execution_support": self.support}
            if self.ack_failures:
                raise self.ack_failures.pop(0)
            return 200, {**envelope, "state": "delivered", **{k: payload[k] for k in c.ACK_FIELDS}, **self.ack_override}
        if stage == "queue_branch":
            head = self.heads[min(self.head_index, len(self.heads) - 1)]
            self.head_index += 1
            return 200, {"ref": "refs/heads/" + c.QUEUE_BRANCH, "object": {"type": "commit", "sha": head}}
        if stage == "queue_read":
            commit = parse.parse_qs(parse.urlsplit(url).query)["ref"][0]
            data = self.files.get(commit)
            if data is None:
                return 404, None
            return 200, {"type": "file", "path": "msw-coding-tasks/" + self.task["id"] + ".json",
                         "encoding": "base64", "size": len(data), "content": base64.b64encode(data).decode()}
        if stage == "queue_write":
            if self.concurrent_content is not None:
                self.files[B] = self.concurrent_content
            if self.write_error is not None:
                raise self.write_error
            self.files[B] = self.tampered_content if self.tampered_content is not None else base64.b64decode(payload["content"])
            return 201, {"commit": {"sha": B}}
        raise AssertionError("Unexpected synthetic stage: " + stage)

    def stage(self, name):
        return [call for call in self.calls if call["stage"] == name]


class TaskTests(unittest.TestCase):
    def test_unicode_golden_fixture_is_byte_exact(self):
        raw = (ROOT / "tests/fixtures/task-utf8.json").read_bytes()
        value = c.decode_json(raw)
        self.assertEqual(c.validate_task(value), raw)
        self.assertEqual(value["request_sha256"], "c5260202d2428fc1db05ae50bc5e5461fb835fb64ba9d9ac69f1ebd72800b17a")
        self.assertEqual(hashlib.sha256(raw).hexdigest(), "18c100dc0241c704e6f77d9cbbfb8733e5bade0954eb339fba8acae833abf90c")
        self.assertIn("\u2028".encode(), raw)
        self.assertIn("\u2029".encode(), raw)
        self.assertIn("🚗".encode(), raw)
        self.assertNotIn(b"\\u2028", raw)

    def test_routing_agrees_with_shared_target_manifest(self):
        targets = json.loads((ROOT / "targets.json").read_text())["targets"]
        self.assertEqual(c.SUPPORT, {name: value["execution_support"] for name, value in targets.items()})

    def test_all_routed_targets_accept_transport(self):
        for target in c.TARGETS:
            with self.subTest(target=target):
                value = seal({**task(), "target": target})
                self.assertTrue(c.validate_task(value).endswith(b"\n"))

    def test_digest_and_identity_bind_payload(self):
        for field, value, expected in [("title", "Changed title", "task_digest_mismatch"),
                                       ("id", "f" * 32, "task_id_mismatch"),
                                       ("request_sha256", "f" * 64, "task_digest_mismatch")]:
            with self.subTest(field=field), self.assertRaisesRegex(c.BridgeError, expected):
                c.validate_task({**task(), field: value})

    def test_key_order_does_not_change_digest(self):
        value = task()
        self.assertEqual(c.validate_task(value), c.validate_task(dict(reversed(list(value.items())))))

    def test_extra_missing_and_wrong_schema_are_rejected(self):
        missing = task()
        missing.pop("objective")
        for value in [None, [], missing, {**task(), "url": "https://evil.example"}, {**task(), "schema": "bad"}]:
            with self.subTest(value=value), self.assertRaisesRegex(c.BridgeError, "invalid_task_schema"):
                c.validate_task(value)

    def test_public_flag_requires_literal_true(self):
        for value in [False, 1, "true", None]:
            with self.subTest(value=value), self.assertRaisesRegex(c.BridgeError, "task_not_public"):
                c.validate_task(seal({**task(), "public_data": value}))

    def test_unicode_whitespace_and_limits_are_enforced(self):
        for field, value in [("title", ""), ("title", "\u00a0\u2028\x1c"), ("title", "x" * 121),
                              ("objective", "x" * 4001), ("acceptance", []), ("acceptance", ["\u2003"]),
                              ("acceptance", ["x"] * 9), ("acceptance", ["x" * 401]), ("target", "unknown")]:
            with self.subTest(field=field, value=value), self.assertRaises(c.BridgeError):
                c.validate_task(seal({**task(), field: value}))

    def test_source_and_request_identifiers_are_bounded_ascii(self):
        for field, value in [("request_id", "short"), ("request_id", "a" * 129), ("request_id", "łańcuch123"),
                              ("request_id", "bad id 123"), ("source_engine", "engine?command=1"),
                              ("source_engine", "a" * 201), ("source_engine", None)]:
            obj = task()
            obj[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(c.BridgeError):
                c.validate_task(obj)

    def test_real_utc_dates_required(self):
        for value in ["2026-02-30T00:00:00Z", "2026-11-07T01:00:00+01:00", "2026-11-07", "2026-11-07T00:00:00.1Z"]:
            with self.subTest(value=value), self.assertRaisesRegex(c.BridgeError, "invalid_timestamp"):
                c.validate_task(seal({**task(), "created_at": value}))

    def test_byte_limit_applies_to_multibyte_text(self):
        value = seal({**task(), "objective": "🚗" * 4000, "acceptance": ["🚗" * 400]})
        with self.assertRaisesRegex(c.BridgeError, "task_too_large"):
            c.validate_task(value)

    def test_known_secret_shapes_are_blocked(self):
        for value in ["ghp_" + "a" * 40, "github_pat_" + "A" * 50,
                      "sk-proj-" + "B" * 50, "-----BEGIN PRIVATE KEY-----"]:
            with self.subTest(value=value), self.assertRaisesRegex(c.BridgeError, "secret_like_task_content"):
                c.validate_task(seal({**task(), "objective": value}))

    def test_duplicate_nonfinite_and_invalid_json_fail_closed(self):
        for raw in [b'{"x":1,"x":2}', b'{"x":{"y":1,"y":2}}', b'{"x":NaN}', b'{"x":Infinity}', b"{", b"\xff"]:
            with self.subTest(raw=raw), self.assertRaises(c.BridgeError):
                c.decode_json(raw)
        for value in [float("nan"), "\ud800"]:
            with self.assertRaisesRegex(c.BridgeError, "invalid_json"):
                c.canonical(value)


class WorkflowAndDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.http, self.env = FakeHTTP(), environment()
        self.client = c.BridgeClient(self.env, self.http)

    def cli(self):
        output = io.StringIO()
        with patch.object(c, "BridgeClient", return_value=self.client), redirect_stdout(output):
            code = c.main(["deliver"], self.env)
        return code, json.loads(output.getvalue())

    def test_only_trusted_workflow_can_obtain_credentials(self):
        for key in ["GITHUB_REPOSITORY", "GITHUB_REF", "GITHUB_WORKFLOW_REF"]:
            with self.subTest(key=key):
                env = {**self.env, key: "untrusted"}
                with self.assertRaisesRegex(c.BridgeError, "untrusted_workflow_context"):
                    c.BridgeClient(env, self.http).run("probe")
                self.assertEqual(self.http.calls, [])

    def test_missing_credentials_and_run_context_fail_before_network(self):
        for key in ["GITHUB_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL", "GITHUB_RUN_ID"]:
            with self.subTest(key=key):
                env = self.env.copy()
                env.pop(key)
                with self.assertRaises(c.BridgeError):
                    c.BridgeClient(env, self.http).run("probe")
                self.assertEqual(self.http.calls, [])

    def test_oidc_url_cannot_send_credentials_to_unsafe_host(self):
        for url in ["https://actions.githubusercontent.com.evil.example/token", "https://evil.example/token",
                    "http://pipelines.actions.githubusercontent.com/token", "https://user@pipelines.actions.githubusercontent.com/token",
                    "https://pipelines.actions.githubusercontent.com:444/token", "https://pipelines.actions.githubusercontent.com:x/token",
                    "https://pipelines.actions.githubusercontent.com/token#fragment", "https://[broken"]:
            with self.subTest(url=url), self.assertRaisesRegex(c.BridgeError, "invalid_oidc_url"):
                c.BridgeClient({**self.env, "ACTIONS_ID_TOKEN_REQUEST_URL": url}, self.http).run("probe")
            self.assertEqual(self.http.calls, [])

    def test_probe_sets_exact_audience_and_does_not_write(self):
        result = self.client.run("probe")
        self.assertEqual(result["state"], "connected")
        self.assertEqual([x["stage"] for x in self.http.calls], ["oidc", "exchange_probe"])
        query = parse.parse_qs(parse.urlsplit(self.http.calls[0]["url"]).query)
        self.assertEqual(query["audience"], [c.AUDIENCE])
        self.assertEqual(query["api-version"], ["2"])

    def test_invalid_operation_cannot_default_to_delivery(self):
        with self.assertRaisesRegex(c.BridgeError, "invalid_operation"):
            self.client.run("launch")
        self.assertEqual(self.http.calls, [])

    def test_hold_and_idle_never_write(self):
        for boundary in ("probe_state", "claim_state"):
            http = FakeHTTP()
            setattr(http, boundary, "held")
            self.assertEqual(c.BridgeClient(self.env, http).run("deliver")["state"], "held")
            self.assertFalse(any(x["stage"].startswith("queue_") for x in http.calls))
        self.http.claim_state = "idle"
        self.assertEqual(self.client.run("deliver")["state"], "idle")
        self.assertEqual(self.http.stage("queue_branch"), [])

    def test_delivery_reads_exact_commit_and_binds_both_digests(self):
        result = self.client.run("deliver")
        self.assertEqual(result["state"], "delivered")
        encoded = c.validate_task(self.http.task)
        self.assertEqual(result["file_sha256"], hashlib.sha256(encoded).hexdigest())
        self.assertNotEqual(result["file_sha256"], result["request_sha256"])
        put = self.http.stage("queue_write")[0]
        self.assertNotIn("sha", put["payload"])
        self.assertEqual(put["payload"]["branch"], c.QUEUE_BRANCH)
        self.assertEqual(base64.b64decode(put["payload"]["content"]), encoded)
        read = self.http.stage("queue_read")[-1]
        self.assertEqual(parse.parse_qs(parse.urlsplit(read["url"]).query), {"ref": [B]})
        self.assertLess(self.http.calls.index(read), self.http.calls.index(self.http.stage("exchange_ack")[0]))
        self.assertFalse(result["code_executed"])
        self.assertFalse(result["production_changed"])

    def test_existing_identical_file_is_acknowledged_without_overwrite(self):
        self.http.files[A] = c.validate_task(self.http.task)
        result = self.client.run("deliver")
        self.assertEqual(result["commit_sha"], A)
        self.assertFalse(result["created"])
        self.assertEqual(self.http.stage("queue_write"), [])

    def test_existing_changed_file_blocks_without_ack(self):
        self.http.files[A] = b"changed\n"
        with self.assertRaisesRegex(c.BridgeError, "immutable_task_conflict"):
            self.client.run("deliver")
        self.assertEqual(self.http.stage("queue_write"), [])
        self.assertEqual(self.http.stage("exchange_ack"), [])

    def test_concurrent_identical_writer_is_recovered_by_readback(self):
        for status in (409, 422):
            with self.subTest(status=status):
                http = FakeHTTP()
                http.write_error = c.BridgeError("http_error", stage="queue_write", status=status)
                http.concurrent_content = c.validate_task(http.task)
                result = c.BridgeClient(self.env, http).run("deliver")
                self.assertEqual(result["state"], "delivered")
                self.assertFalse(result["created"])
                self.assertEqual(len(http.stage("queue_write")), 1)

    def test_branch_movement_without_matching_file_does_not_repeat_put(self):
        self.http.write_error = c.BridgeError("http_error", stage="queue_write", status=409)
        with self.assertRaisesRegex(c.BridgeError, "immutable_task_conflict"):
            self.client.run("deliver")
        self.assertEqual(len(self.http.stage("queue_write")), 1)
        self.assertEqual(self.http.stage("exchange_ack"), [])

    def test_bad_commit_readback_cannot_be_acknowledged(self):
        self.http.tampered_content = b"different content\n"
        with self.assertRaisesRegex(c.BridgeError, "immutable_task_conflict"):
            self.client.run("deliver")
        self.assertEqual(self.http.stage("exchange_ack"), [])

    def test_write_timeout_is_unknown_without_duplicate_put(self):
        self.http.write_error = c.BridgeError("network_error", stage="queue_write")
        code, receipt = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(receipt["state"], "write_unconfirmed")
        self.assertEqual(len(self.http.stage("queue_write")), 1)
        self.assertEqual(self.http.stage("exchange_ack"), [])

    def test_expired_lease_and_wrong_route_cannot_start_write(self):
        for field, value in [("expiry", "2000-01-01T00:00:00Z"), ("lease", "bad"), ("support", "execute_anything")]:
            http = FakeHTTP()
            setattr(http, field, value)
            with self.subTest(field=field), self.assertRaises(c.BridgeError):
                c.BridgeClient(self.env, http).run("deliver")
            self.assertEqual(http.stage("queue_branch"), [])

    def test_task_tamper_is_rejected_before_any_github_operation(self):
        self.http.task["objective"] = "Changed after admission"
        with self.assertRaisesRegex(c.BridgeError, "task_digest_mismatch"):
            self.client.run("deliver")
        self.assertEqual(self.http.stage("queue_branch"), [])

    def test_exchange_run_identity_cannot_change(self):
        for operation in ("probe", "claim", "ack"):
            http = FakeHTTP()
            http.run_override[operation] = "different-run"
            with self.subTest(operation=operation), self.assertRaisesRegex(c.BridgeError, "exchange_run_mismatch"):
                c.BridgeClient(self.env, http).run("deliver")

    def test_ack_must_bind_every_public_identity_field(self):
        for field in c.ACK_FIELDS:
            http = FakeHTTP()
            http.ack_override[field] = "wrong"
            client = c.BridgeClient(self.env, http)
            with self.subTest(field=field), self.assertRaisesRegex(c.BridgeError, "delivery_not_confirmed"):
                client.run("deliver")
            self.assertEqual(client.receipt["state"], "stored_unacknowledged")

    def test_ack_retry_is_bounded_and_keeps_exact_same_identity(self):
        self.http.ack_failures = [c.BridgeError("http_error", stage="exchange_ack", status=503)]
        with patch.object(c.time, "sleep") as sleep:
            self.assertEqual(self.client.run("deliver")["state"], "delivered")
        acks = self.http.stage("exchange_ack")
        self.assertEqual(len(acks), 2)
        self.assertEqual(acks[0]["payload"], acks[1]["payload"])
        self.assertEqual(acks[0]["payload"]["lease_token"], LEASE)
        sleep.assert_called_once_with(1)

    def test_unconfirmed_ack_retains_known_commit_in_public_receipt(self):
        self.http.ack_failures = [c.BridgeError("network_error", stage="exchange_ack") for _ in range(2)]
        with patch.object(c.time, "sleep"):
            code, result = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(result["state"], "stored_unacknowledged")
        self.assertEqual(result["commit_sha"], B)
        self.assertEqual(len(self.http.stage("exchange_ack")), 2)

    def test_auth_denial_does_not_retry_ack(self):
        self.http.ack_failures = [c.BridgeError("http_error", stage="exchange_ack", status=403)]
        with self.assertRaises(c.BridgeError):
            self.client.run("deliver")
        self.assertEqual(len(self.http.stage("exchange_ack")), 1)

    def test_receipt_and_summary_do_not_include_tokens_lease_or_task_text(self):
        result = self.client.run("deliver")
        with tempfile.TemporaryDirectory() as directory:
            env = {"MSW_BRIDGE_RECEIPT_PATH": directory + "/receipt.json", "GITHUB_STEP_SUMMARY": directory + "/summary.md"}
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                c.write_public_receipt(result, env)
            written = stdout.getvalue() + Path(env["MSW_BRIDGE_RECEIPT_PATH"]).read_text() + Path(env["GITHUB_STEP_SUMMARY"]).read_text()
            for secret in [JWT, LEASE, self.env["GITHUB_TOKEN"], self.env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"], self.http.task["objective"]]:
                self.assertNotIn(secret, written)

    def test_unexpected_exception_is_redacted(self):
        with patch.object(self.http, "request", side_effect=RuntimeError("private-token-value")):
            code, result = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(result["error"], "unexpected_client_error")
        self.assertNotIn("private-token-value", json.dumps(result))


class Response:
    def __init__(self, raw=b'{"ok":true}', headers=None, status=200):
        self.raw, self.headers, self.status = raw, headers or {}, status
        self.reads = []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, size):
        self.reads.append(size)
        return self.raw[:size]


class HTTPBoundaryTests(unittest.TestCase):
    def client_with(self, response):
        class Opener:
            def __init__(self): self.calls = []
            def open(self, req, timeout):
                self.calls.append((req, timeout))
                if isinstance(response, Exception): raise response
                return response
        opener = Opener()
        client = c.JsonHTTP()
        client.opener = opener
        return client, opener

    def test_github_headers_and_timeout_are_pinned(self):
        client, opener = self.client_with(Response())
        self.assertEqual(client.request("GET", c.API_ROOT, "synthetic-token", stage="test"), (200, {"ok": True}))
        req, timeout = opener.calls[0]
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(req.get_header("X-github-api-version"), "2026-03-10")
        self.assertEqual(req.get_header("Authorization"), "Bearer synthetic-token")
        self.assertEqual(timeout, 15)

    def test_insecure_and_ambiguous_urls_never_send_authentication(self):
        client, opener = self.client_with(Response())
        for url in ["http://api.github.com", "https://user:pass@api.github.com", "https://api.github.com:444/x", "https://api.github.com:x", "https://[bad", "https://api.github.com/x#y"]:
            with self.subTest(url=url), self.assertRaisesRegex(c.BridgeError, "invalid_transport_url"):
                client.request("GET", url, "secret", stage="test")
        self.assertEqual(opener.calls, [])

    def test_redirects_and_error_bodies_are_not_consumed(self):
        for status in [302, 403]:
            body = io.BytesIO(b"private response contents")
            failure = error.HTTPError(c.API_ROOT, status, "error", {}, body)
            client, _ = self.client_with(failure)
            with self.subTest(status=status), self.assertRaises(c.BridgeError) as raised:
                client.request("GET", c.API_ROOT, "secret", stage="test")
            self.assertNotIn("private", str(raised.exception))
            self.assertTrue(body.closed)
        self.assertIsNone(c.NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.example"))

    def test_allowed_404_returns_empty_response(self):
        client, _ = self.client_with(error.HTTPError(c.API_ROOT, 404, "Not found", {}, None))
        self.assertEqual(client.request("GET", c.API_ROOT, "token", stage="test", allowed=(200, 404)), (404, None))

    def test_response_size_is_bounded_even_when_header_lies(self):
        for response in [Response(b" " * 50, {"Content-Length": "1"}), Response(headers={"Content-Length": "99999"}), Response(headers={"Content-Length": "invalid"})]:
            client, _ = self.client_with(response)
            with self.assertRaisesRegex(c.BridgeError, "response_too_large"):
                client.request("GET", c.API_ROOT, "token", stage="test", max_bytes=20)
            self.assertTrue(all(size == 21 for size in response.reads))

    def test_response_json_ambiguity_has_fixed_public_error(self):
        client, _ = self.client_with(Response(b'{"x":1,"x":2}'))
        with self.assertRaisesRegex(c.BridgeError, "duplicate_json_key") as raised:
            client.request("GET", c.API_ROOT, "token", stage="queue_read")
        self.assertEqual(raised.exception.stage, "queue_read")


if __name__ == "__main__":
    unittest.main()
