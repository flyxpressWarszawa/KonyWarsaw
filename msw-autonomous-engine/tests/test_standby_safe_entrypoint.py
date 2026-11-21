"""The standby wrapper must preserve the verified overlay and refuse drift."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, mock


PATH = Path(__file__).resolve().parents[1] / "integrations/hermes_codex_native_v1/standby_safe_entrypoint.py"
spec = importlib.util.spec_from_file_location("standby_safe_entrypoint", PATH)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def verified_result(**changes):
    selected = {}
    for name in entry.OVERLAY_MODULES:
        selected[name] = {"source": "overlay", "origin": str(entry.OVERLAY.parent / "overlay" / "msw_engine" / (name.split(".")[-1] + ".py"))}
    for name in ("__init__", "github_python_hub_once", "python_receipt_attestor",
                 "python_task_pilot", "tasks", "wp_registry_staging"):
        selected["msw_engine." + name] = {"source": "public", "origin": "/opt/render/project/src/msw-autonomous-engine/msw_engine/" + name + ".py"}
    report = {"version": 7, "selected_modules": selected, "public_modules_verified": 10,
              "overlay_modules_verified": 5, "environment_modified": False,
              "public_head_matches_tested": True}
    report.update(changes)
    return SimpleNamespace(returncode=0, stdout=json.dumps({
        "staging_overlay_verification": report, "verify_only": True,
        "entrypoint_started": False, "verification_network_calls": 0,
    }))


class StandbyEntrypointTests(TestCase):
    def test_verified_overlay_execs_one_existing_transport_with_codex_disabled(self):
        with TemporaryDirectory() as root, mock.patch.object(entry, "OVERLAY", Path(root) / "overlay_launcher.py"), \
                mock.patch.object(entry.subprocess, "run", return_value=verified_result()), \
                mock.patch.object(entry.os, "execve") as execve:
            # Origins in the test result use the original disk path. Rebuild them
            # against the temporary path before asserting the actual exec.
            result = verified_result()
            data = json.loads(result.stdout)
            for item in data["staging_overlay_verification"]["selected_modules"].values():
                if item["source"] == "overlay":
                    item["origin"] = item["origin"].replace(
                        "/tmp/msw-aux-seo/hermes-chat-staging/overlay-v7-repinned-a4dea", root)
            entry.subprocess.run.return_value.stdout = json.dumps(data)
            entry.OVERLAY.write_text("# existing launcher")
            self.assertEqual(entry.main(), 0)
            execve.assert_called_once()
            self.assertEqual(execve.call_args.args[1][-1], str(entry.OVERLAY))
            self.assertEqual(execve.call_args.args[2]["MSW_CODEX_PRODUCTION_DISPATCH"], "0")

    def test_public_module_substitution_fails_closed_before_exec(self):
        with TemporaryDirectory() as root, mock.patch.object(entry, "OVERLAY", Path(root) / "overlay_launcher.py"), \
                mock.patch.object(entry.os, "execve") as execve:
            entry.OVERLAY.write_text("# existing launcher")
            result = verified_result()
            payload = json.loads(result.stdout)
            selected = payload["staging_overlay_verification"]["selected_modules"]
            selected["msw_engine.unrecognized"] = selected.pop("msw_engine.tasks")
            for item in selected.values():
                if item["source"] == "overlay":
                    item["origin"] = item["origin"].replace(
                        "/tmp/msw-aux-seo/hermes-chat-staging/overlay-v7-repinned-a4dea", root)
            result.stdout = json.dumps(payload)
            with mock.patch.object(entry.subprocess, "run", return_value=result):
                self.assertEqual(entry.main(), 78)
            execve.assert_not_called()

    def test_spoofed_module_origins_fail_closed_before_exec(self):
        # Exact module names and correct counts are not enough if paths can be
        # substituted with another admitted module or a traversal suffix.
        for key, forged in (
            ("msw_engine.tasks",
             "/opt/render/project/src/msw-autonomous-engine/msw_engine/"
             "wp_registry_staging.py"),
            ("msw_engine.tasks",
             "/opt/render/project/src/msw-autonomous-engine/msw_engine/"
             "../outside.py"),
            ("msw_engine.github_chat_queue",
             str(entry.OVERLAY.parent / "overlay" / "msw_engine"
                 / "github_queue_staging.py")),
        ):
            with self.subTest(module=key, forged=forged):
                result = verified_result()
                payload = json.loads(result.stdout)
                payload["staging_overlay_verification"]["selected_modules"][key]["origin"] = forged
                result.stdout = json.dumps(payload)
                with mock.patch.object(entry.subprocess, "run", return_value=result):
                    self.assertIsNone(entry.preflight())

    def test_public_head_mismatch_holds_without_exec(self):
        # A valid 11-module overlay is not sufficient when the public source
        # differs from the tested release. Non-booleans must also fail closed.
        for drift in (False, None, 0, "true"):
            with self.subTest(public_head_matches_tested=drift), TemporaryDirectory() as root, \
                    mock.patch.object(entry, "OVERLAY", Path(root) / "overlay_launcher.py"), \
                    mock.patch.object(entry.os, "execve") as execve:
                entry.OVERLAY.write_text("# existing launcher")
                result = verified_result(public_head_matches_tested=drift)
                payload = json.loads(result.stdout)
                for item in payload["staging_overlay_verification"]["selected_modules"].values():
                    if item["source"] == "overlay":
                        item["origin"] = item["origin"].replace(
                            "/tmp/msw-aux-seo/hermes-chat-staging/overlay-v7-repinned-a4dea", root)
                result.stdout = json.dumps(payload)
                with mock.patch.object(entry.subprocess, "run", return_value=result):
                    self.assertEqual(entry.main(), 78)
                execve.assert_not_called()

    def test_noninteger_preflight_counters_hold_before_exec(self):
        # Python equality treats False == 0 and float(7) == 7. A forged
        # successful-looking report is not an exact offline attestation.
        cases = (
            ("network_false", "outer", "verification_network_calls", False),
            ("network_float", "outer", "verification_network_calls", 0.0),
            ("version_float", "report", "version", 7.0),
            ("public_float", "report", "public_modules_verified", 10.0),
            ("overlay_float", "report", "overlay_modules_verified", 5.0),
            ("public_string", "report", "public_modules_verified", "10"),
            ("overlay_null", "report", "overlay_modules_verified", None),
        )
        for label, target, field, forged in cases:
            with self.subTest(label=label), TemporaryDirectory() as root, \
                    mock.patch.object(entry, "OVERLAY", Path(root) / "overlay_launcher.py"), \
                    mock.patch.object(entry.os, "execve") as execve:
                entry.OVERLAY.write_text("# existing launcher")
                result = verified_result()
                payload = json.loads(result.stdout)
                node = payload if target == "outer" else payload["staging_overlay_verification"]
                node[field] = forged
                for item in payload["staging_overlay_verification"]["selected_modules"].values():
                    if item["source"] == "overlay":
                        item["origin"] = item["origin"].replace(
                            "/tmp/msw-aux-seo/hermes-chat-staging/overlay-v7-repinned-a4dea", root)
                result.stdout = json.dumps(payload)
                with mock.patch.object(entry.subprocess, "run", return_value=result):
                    self.assertEqual(entry.main(), 78)
                execve.assert_not_called()

    def test_ambiguous_json_attestation_fails_before_start(self):
        # Python's permissive json.loads would overwrite an earlier duplicate
        # verification key; NaN/Infinity are also nonstandard JSON constants.
        original = verified_result().stdout
        unsafe = (
            original.replace('"version": 7', '"version": 6, "version": 7', 1),
            original.replace('"selected_modules": {',
                             '"selected_modules": {}, "selected_modules": {', 1),
            original.replace('"version": 7', '"version": NaN, "version": 7', 1),
            original.replace('"version": 7', '"version": Infinity, "version": 7', 1),
        )
        for malformed in unsafe:
            self.assertNotEqual(malformed, original)
            response = SimpleNamespace(returncode=0, stdout=malformed)
            with mock.patch.object(entry.subprocess, "run", return_value=response):
                self.assertIsNone(entry.preflight())
        # No weaker regression: the normal unchanged verifier output is valid.
        with mock.patch.object(entry.subprocess, "run", return_value=verified_result()):
            self.assertIsNotNone(entry.preflight())

    def test_missing_or_changed_overlay_cannot_start(self):
        with TemporaryDirectory() as root, mock.patch.object(entry, "OVERLAY", Path(root) / "overlay_launcher.py"), \
                mock.patch.object(entry.os, "execve") as execve:
            self.assertEqual(entry.main(), 78)
            entry.OVERLAY.write_text("# existing launcher")
            with mock.patch.object(entry.subprocess, "run", return_value=verified_result(overlay_modules_verified=4)):
                self.assertEqual(entry.main(), 78)
            execve.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
