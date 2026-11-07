#!/usr/bin/env python3
"""Deterministic GitHub development gate. No deployment or live-service calls.

Run a trusted copy of this file and its policy from the BASE commit. Tests execute
candidate application code with no inherited production credentials; workflow
network/sandbox controls remain responsible for isolating that untrusted code.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

SCHEMA = "msw.github-engine.policy.v1"
PREFIXES = ("msw-ai-technical-core/", "msw-ai-centrum-robotow/app/",
            "msw-ai-centrum-robotow/tests/", "msw-compute-node-adapters/", "msw-research-engine/")
REPORT_DIR = ".msw-github-engine"
SECRET_RE = re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{35,}|AKIA[A-Z0-9]{16})\b")
PROTECTED_NAMES = {"dockerfile", "render.yaml", "render.yml", "package.json", "package-lock.json",
                   "pyproject.toml", "pipfile", "pipfile.lock", "poetry.lock", "uv.lock", "yarn.lock",
                   "pnpm-lock.yaml", "composer.json", "composer.lock", "credentials", "credentials.json"}
SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".css", ".html", ".md", ".json", ".txt"}
PYTHON = sys.executable

# Commands never come from repository data, model output or a shell expression.
REGISTRY = {
    "technical-core": {"cwd": "msw-ai-technical-core", "command": ["node", "--test", "--test-reporter=tap", "tests/smoke.test.mjs"], "kind": "node", "minimum": 9},
    "compute-contracts": {"cwd": ".", "command": ["node", "--test", "--test-reporter=tap", "msw-compute-node-adapters/test-compute-node.mjs"], "kind": "node", "minimum": 14},
    "compute-disabled": {"cwd": ".", "command": ["node", "msw-compute-node-adapters/integration-smoke.mjs", "disabled"], "kind": "integration", "minimum": 12},
    "compute-enabled": {"cwd": ".", "command": ["node", "msw-compute-node-adapters/integration-smoke.mjs", "enabled"], "kind": "integration", "minimum": 17},
    "research": {"cwd": ".", "command": [PYTHON, "-m", "unittest", "discover", "-s", "msw-research-engine/tests", "-p", "test_sync.py", "-v"], "kind": "unittest", "minimum": 5},
    "platform-core": {"cwd": "msw-ai-centrum-robotow", "command": [PYTHON, "-m", "unittest", "discover", "-s", "tests", "-p", "test_platform_core.py", "-v"], "kind": "unittest", "minimum": 5},
    "portal-api": {"cwd": "msw-ai-centrum-robotow", "command": [PYTHON, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_api.py"], "kind": "pytest", "minimum": 9},
}


class Blocked(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Blocked("duplicate_policy_key")
        result[key] = value
    return result


def load_policy(path):
    try:
        path = Path(path)
        if path.is_symlink() or path.stat().st_size > 32768:
            raise Blocked("invalid_policy_file")
        p = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        expected = {"schema", "enabled", "active_from", "active_until", "max_files", "max_bytes", "allowed_prefixes", "priorities"}
        if set(p) != expected or p["schema"] != SCHEMA or type(p["enabled"]) is not bool:
            raise Blocked("invalid_policy_schema")
        start, end = (datetime.fromisoformat(p[k]) for k in ("active_from", "active_until"))
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise Blocked("invalid_policy_window")
        if any(type(p[k]) is not int or not 1 <= p[k] <= maximum for k, maximum in (("max_files", 12), ("max_bytes", 204800))):
            raise Blocked("invalid_policy_limits")
        if p["allowed_prefixes"] != list(PREFIXES) or not isinstance(p["priorities"], list) or not p["priorities"] or not all(isinstance(x, str) and 0 < len(x) < 500 for x in p["priorities"]):
            raise Blocked("invalid_policy_scope")
        return p
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise Blocked("policy_invalid") from error


def gate(policy_path, now=None, environ=None):
    env = os.environ if environ is None else environ
    try:
        p = load_policy(policy_path)
        now = datetime.now(timezone.utc) if now is None else now
        if now.tzinfo is None:
            raise Blocked("clock_invalid")
        override = env.get("MSW_GITHUB_ENGINE_ENABLED", "1").lower()
        if override not in {"0", "1", "false", "true"}:
            return {"enabled": False, "reason": "invalid_enable_override"}
        if not p["enabled"] or override in {"0", "false"}:
            return {"enabled": False, "reason": "disabled"}
        if now < datetime.fromisoformat(p["active_from"]):
            return {"enabled": False, "reason": "not_started"}
        if now >= datetime.fromisoformat(p["active_until"]):
            return {"enabled": False, "reason": "expired"}
        return {"enabled": True, "reason": "active", "active_until": p["active_until"]}
    except Blocked as error:
        return {"enabled": False, "reason": str(error)}


def git(root, *args, binary=False):
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
    if result.returncode:
        raise Blocked("git_read_failed")
    return result.stdout if binary else result.stdout.decode("utf-8", errors="strict").strip()


def is_test(path):
    p = PurePosixPath(path)
    return ("tests" in p.parts or p.name.startswith(("test_", "test-"))
            or ".test." in p.name or p.name == "integration-smoke.mjs")


def checked_path(path, policy):
    p = PurePosixPath(path)
    parts = p.parts
    if not parts or p.is_absolute() or any(x in {".", ".."} or x.startswith(".") for x in parts) or any(ord(x) < 32 for x in path) or "\\" in path:
        raise Blocked("unsafe_path")
    if not path.startswith(tuple(policy["allowed_prefixes"])):
        raise Blocked("protected_path")
    low = p.name.lower()
    if (any(x.lower() in {"data", "runtime", "secrets", "credentials", "node_modules", "__pycache__"} for x in parts)
            or low in PROTECTED_NAMES or low.startswith(("requirements", "docker-compose", "compose.", "worker.env", "deploy", "secret", "credentials"))
            or ".env" in low or p.suffix.lower() not in SUFFIXES):
        raise Blocked("protected_path")


def tree(root, base):
    if not re.fullmatch(r"[a-fA-F0-9]{40,64}", base):
        raise Blocked("base_must_be_full_commit_sha")
    git(root, "cat-file", "-e", base + "^{commit}")
    entries = {}
    for raw in git(root, "ls-tree", "-rz", "--full-tree", base, binary=True).split(b"\0"):
        if raw:
            metadata, path = raw.split(b"\t", 1)
            mode, kind, sha = metadata.decode().split()
            entries[path.decode("utf-8")] = (mode, kind, sha)
    return entries


def inspect_changes(root, base, policy):
    original = tree(root, base)
    raw = git(root, "diff", "--name-only", "--no-renames", "-z", base, "--", binary=True)
    staged = git(root, "diff", "--cached", "--name-only", "--no-renames", "-z", base, "--", binary=True)
    staged_names = {x.decode("utf-8") for x in staged.split(b"\0") if x}
    index = {}
    for item in git(root, "ls-files", "--stage", "-z", binary=True).split(b"\0"):
        if item:
            metadata, path = item.split(b"\t", 1)
            mode, sha, stage = metadata.decode().split()
            if stage != "0":
                raise Blocked("unmerged_index")
            index[path.decode("utf-8")] = (mode, sha)
    raw += staged
    raw += git(root, "ls-files", "--others", "--exclude-standard", "-z", binary=True)
    names = sorted({x.decode("utf-8") for x in raw.split(b"\0") if x})
    # Runtime artifacts are irrelevant only while untracked. Never hide a
    # staged/committed payload under an otherwise ignored report/cache path.
    names = [p for p in names if not ((p.startswith(REPORT_DIR + "/") or "__pycache__" in PurePosixPath(p).parts)
                                    and p not in index and p not in original)]
    if len(names) > policy["max_files"]:
        raise Blocked("file_limit")
    changes, size = [], 0
    for path in names:
        checked_path(path, policy)
        if path in original and is_test(path):
            raise Blocked("existing_test_changed_or_deleted")
        target = root / path
        if path in index and index[path][0] not in {"100644", "100755"}:
            raise Blocked("gitlink_or_link_forbidden")
        # Includes symlinks in parent directories and deleted gitlinks.
        if any((root / Path(*PurePosixPath(path).parts[:i])).is_symlink() for i in range(1, len(PurePosixPath(path).parts) + 1)):
            raise Blocked("symlink_forbidden")
        if path in original and original[path][0] not in {"100644", "100755"}:
            raise Blocked("gitlink_or_link_forbidden")
        if target.exists():
            information = target.stat()
            if not stat.S_ISREG(information.st_mode) or information.st_nlink != 1:
                raise Blocked("nonregular_file")
            if information.st_size + size > policy["max_bytes"]:
                raise Blocked("byte_limit")
            if path in staged_names and (path not in index or git(root, "hash-object", "--no-filters", "--", path) != index[path][1]):
                raise Blocked("index_worktree_mismatch")
            raw = target.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeError as error:
                raise Blocked("binary_file_forbidden") from error
            if "\x00" in text or SECRET_RE.search(text):
                raise Blocked("secret_or_binary_content")
            size += len(raw)
            change = "modified" if path in original else "added"
        else:
            if path in staged_names and path in index:
                raise Blocked("index_worktree_mismatch")
            if path not in original:
                raise Blocked("file_disappeared")
            size += int(git(root, "cat-file", "-s", original[path][2]))
            change = "deleted"
        if size > policy["max_bytes"]:
            raise Blocked("byte_limit")
        changes.append({"path": path, "change": change,
                        "sha256": hashlib.sha256(raw).hexdigest() if change != "deleted" else None})
    return changes


def select_checks(changes, all_checks=False):
    selected = set(REGISTRY) if all_checks else set()
    added = {}
    for change in changes:
        path = change["path"]
        if path.startswith("msw-ai-technical-core/"):
            selected.add("technical-core")
            if "/market/" in path:
                selected.update(("compute-contracts", "compute-disabled", "compute-enabled"))
        elif path.startswith("msw-compute-node-adapters/"):
            selected.update(("compute-contracts", "compute-disabled", "compute-enabled"))
        elif path.startswith("msw-research-engine/"):
            selected.add("research")
        elif path.startswith("msw-ai-centrum-robotow/"):
            selected.update(("platform-core", "portal-api"))
        if change["change"] != "added" or not is_test(path):
            continue
        p = PurePosixPath(path)
        if p.suffix in {".js", ".mjs", ".cjs"}:
            check = {"cwd": ".", "command": ["node", "--test", "--test-reporter=tap", path], "kind": "node", "minimum": 1}
        elif p.suffix == ".py" and p.name.startswith("test"):
            portal = path.startswith("msw-ai-centrum-robotow/")
            cwd = "msw-ai-centrum-robotow" if portal else "."
            testpath = str(p.relative_to(cwd)) if portal else path
            check = {"cwd": cwd, "command": [PYTHON, "-m", "pytest", "-q", "-p", "no:cacheprovider", testpath] if portal else
                     [PYTHON, "-m", "unittest", "discover", "-s", str(p.parent), "-p", p.name, "-v"],
                     "kind": "pytest" if portal else "unittest", "minimum": 1}
        else:
            raise Blocked("new_test_format_unsupported")
        added["new:" + path] = check
    return {**{k: v for k, v in REGISTRY.items() if k in selected}, **added}


def observed_count(kind, output):
    if kind == "integration":
        for line in output.splitlines():
            try:
                result = json.loads(line)
                if result.get("schema") == "msw.compute.integration-test.v1" and result.get("passed") is True:
                    return int(result["checks"])
            except (ValueError, TypeError, AttributeError, KeyError):
                pass
        return 0
    patterns = {"node": r"^# pass (\d+)\s*$", "unittest": r"Ran (\d+) tests? in", "pytest": r"(?:^|[,\s])(\d+) passed(?:\s|,|$)"}
    match = re.search(patterns[kind], output, re.MULTILINE)
    count = int(match.group(1)) if match else 0
    skipped = re.search(r"skipped=(\d+)", output) if kind == "unittest" else None
    return max(0, count - (int(skipped.group(1)) if skipped else 0))


def sanitized_tail(output):
    output = SECRET_RE.sub("[REDACTED]", output)
    output = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:bearer|basic)\s+[^\s,}]+", r"\1[REDACTED]", output)
    output = re.sub(r"(?i)((?:authorization|password|secret|token|api[_-]?key)\s*[:=]\s*)(?:['\"][^'\"]+['\"]|[^\s,}]+)", r"\1[REDACTED]", output)
    return output[-4000:]


def run_check(name, spec, directory):
    started = time.monotonic()
    evidence = {"id": name, "command": spec["command"], "cwd": spec["cwd"], "status": "FAIL", "exit_code": None, "checks": 0}
    if spec["kind"] == "pytest":
        missing = [x for x in ("pytest", "httpx", "fastapi", "sqlalchemy") if importlib.util.find_spec(x) is None]
        if missing:
            return {**evidence, "status": "BLOCKED", "reason": "dependencies_missing", "missing": missing, "duration_seconds": 0}
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT") if k in os.environ}
    env.update(HOME=str(directory), TMPDIR=str(directory), PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", CI="1", NO_COLOR="1")
    try:
        with tempfile.TemporaryFile() as log:
            result = subprocess.run(spec["command"], cwd=directory / spec["cwd"], env=env,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=180, check=False, shell=False)
            total = log.tell()
            log.seek(max(0, total - 262144))
            raw = log.read(262144)
        output = raw.decode("utf-8", errors="replace")
        count = observed_count(spec["kind"], output)
        evidence.update(exit_code=result.returncode, checks=count, output_sha256=hashlib.sha256(raw).hexdigest())
        evidence["status"] = "PASS" if result.returncode == 0 and count >= spec["minimum"] else "FAIL"
        if count < spec["minimum"]:
            evidence["reason"] = "missing_or_reduced_test_execution"
        if evidence["status"] != "PASS":
            evidence["output_tail"] = sanitized_tail(output)
    except subprocess.TimeoutExpired:
        evidence["reason"] = "test_timeout"
    except OSError:
        evidence.update(status="BLOCKED", reason="test_command_unavailable")
    evidence["duration_seconds"] = round(time.monotonic() - started, 3)
    return evidence


def run_checks(root, base, changes, checks):
    if not checks:
        raise Blocked("no_tests_selected")
    archive = git(root, "archive", "--format=tar", base, binary=True)
    if len(archive) > 33554432:
        raise Blocked("snapshot_limit")
    with tempfile.TemporaryDirectory(prefix="msw-github-check-") as temporary:
        directory = Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            for entry in source.getmembers():
                if entry.issym() or entry.islnk() or not (entry.isfile() or entry.isdir()):
                    raise Blocked("baseline_archive_link_forbidden")
            source.extractall(directory, filter="data")
        for change in changes:
            target = directory / change["path"]
            if change["change"] == "deleted":
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / change["path"], target)
        return [run_check(name, spec, directory) for name, spec in checks.items()]


def evaluate(root, base, policy, baseline=False):
    changes = [] if baseline else inspect_changes(root, base, policy)
    if not baseline and not changes:
        return {"status": "NO_CHANGES", "base_sha": base, "changes": [], "tests": []}
    checks = select_checks(changes, all_checks=baseline)
    tests = run_checks(root, base, changes, checks)
    if not tests:
        raise Blocked("no_test_evidence")
    status = "PASS" if all(x["status"] == "PASS" for x in tests) else ("BLOCKED" if any(x["status"] == "BLOCKED" for x in tests) else "FAIL")
    # Reject changes made to the real working tree while verification was running.
    if not baseline and changes != inspect_changes(root, base, policy):
        raise Blocked("working_tree_changed_during_tests")
    return {"status": status, "base_sha": base, "changes": changes, "tests": tests, "production_verified": False}


def save_report(root, name, report):
    target = root / REPORT_DIR
    if target.is_symlink():
        raise Blocked("report_directory_symlink")
    target.mkdir(exist_ok=True)
    file = target / (name + ".json")
    temporary = target / (name + ".tmp")
    if file.is_symlink() or temporary.is_symlink():
        raise Blocked("report_file_symlink")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(file)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", default=str(Path(__file__).with_name("policy.json")))
    sub = parser.add_subparsers(dest="operation", required=True)
    for name in ("gate", "context", "baseline"):
        sub.add_parser(name)
    sub.add_parser("validate").add_argument("--base", required=True)
    args = parser.parse_args(argv)
    if args.operation == "gate":
        result = gate(args.policy)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write("enabled=" + str(result["enabled"]).lower() + "\nreason=" + result["reason"] + "\n")
        print(json.dumps(result))
        return 2 if result["reason"] in {"policy_invalid", "clock_invalid", "invalid_enable_override"} else 0
    root = None
    try:
        policy = load_policy(args.policy)
        if args.operation == "context":
            print(json.dumps({"schema": SCHEMA, "priorities": policy["priorities"], "allowed_prefixes": list(PREFIXES), "test_registry": REGISTRY, "test_files": "Existing tests are immutable; every new test is executed.", "live_execution": "Existing authenticated COMBO/Render/WordPress lanes only."}, ensure_ascii=False, indent=2))
            return 0
        root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
        base = git(root, "rev-parse", "HEAD") if args.operation == "baseline" else args.base
        report = evaluate(root, base, policy, baseline=args.operation == "baseline")
    except (Blocked, OSError, UnicodeError, subprocess.SubprocessError) as error:
        report = {"status": "BLOCKED", "reason": str(error) if isinstance(error, Blocked) else type(error).__name__, "tests": []}
    if root is not None:
        try:
            save_report(root, args.operation, report)
        except (Blocked, OSError) as error:
            report = {"status": "BLOCKED", "reason": str(error) if isinstance(error, Blocked) else "report_write_failed", "tests": []}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"PASS", "NO_CHANGES"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
