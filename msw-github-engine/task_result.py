#!/usr/bin/env python3
"""Bind trusted local validation to a pinned task and independently read its PR.

Use this module, task_context.py, controller.py and their policy/catalog from the
trusted base. The context digest comes from a different trusted job. A report
must come from that job's isolated controller.validate invocation, not from a
candidate-selected file. This verifier does not execute candidate code or grant
permission, publish a PR, acknowledge admission, merge or deploy anything.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import importlib.util
import math
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess


SOURCE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("msw_result_context", SOURCE / "task_context.py")
task_context = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(task_context)
bridge, controller = task_context.bridge, task_context.controller
Blocked = task_context.Blocked

PROOF_SCHEMA = "msw.coding-task-proof.v1"
BINDING_SCHEMA = "msw.coding-task-binding.v1"
RESULT_SCHEMA = "msw.coding-task-result.v1"
START = "<!-- msw-coding-task-binding:start -->"
END = "<!-- msw-coding-task-binding:end -->"
REPOSITORY_ID = 405190198
MAX_ARTIFACT = 1048576
TRUE_FLAGS = ("task_link_verified", "local_tests_verified", "production_hold")
FALSE_FLAGS = ("acceptance_verified", "pr_verified", "central_ack_verified",
               "merged_verified", "deployed_verified", "execution_authorized")
PROOF_KEYS = {"schema", "status", "context_sha256", "binding", "base_sha", "commit",
              "tree", "branch", "changes", "tests", "validation_sha256",
              "pr_binding_block", *TRUE_FLAGS, *FALSE_FLAGS}
CHANGE_KEYS = {"path", "change", "sha256", "git_blob_sha"}


def _sha(value, *, length=40, code="invalid_sha"):
    pattern = bridge.SHA if length == 40 else bridge.HEX64
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise Blocked(code)
    return value


def _canonical(value):
    try:
        return bridge.canonical(value) + b"\n"
    except (bridge.BridgeError, ValueError, TypeError, OverflowError, RecursionError):
        raise Blocked("invalid_json") from None


def _json(raw):
    try:
        return bridge.decode_json(raw)
    except (bridge.BridgeError, ValueError, TypeError, OverflowError, RecursionError):
        raise Blocked("invalid_json") from None


def _read(path, maximum=MAX_ARTIFACT):
    """A trusted workflow chooses the file; reject links and unbounded data."""
    try:
        with open(path, "rb", opener=lambda p, f: os.open(p, f | os.O_NOFOLLOW)) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= maximum:
                raise Blocked("invalid_artifact_file")
            raw = stream.read(maximum + 1)
        if len(raw) > maximum:
            raise Blocked("artifact_too_large")
        return raw
    except OSError:
        raise Blocked("artifact_read_failed") from None


def pinned_context(context, pin, *, repository_root=None):
    _sha(pin, length=64, code="invalid_context_pin")
    raw = task_context.context_bytes(context, repository_root=repository_root)
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), pin):
        raise Blocked("context_pin_mismatch")
    return context


def load_context(path, pin, *, repository_root=None):
    raw = _read(path, 65536)
    context = pinned_context(_json(raw), pin, repository_root=repository_root)
    if raw != task_context.context_bytes(context, repository_root=repository_root):
        raise Blocked("noncanonical_context_file")
    return context


def expected_binding(context, pin, *, repository_root=None):
    pinned_context(context, pin, repository_root=repository_root)
    keys = ("repository", "queue_branch", "queue_commit", "queue_head_sha", "task_path",
            "task_id", "request_sha256", "file_sha256", "target", "base_branch",
            "base_sha", "allowed_paths")
    return {"schema": BINDING_SCHEMA, "context_sha256": pin,
            **{key: context[key] for key in keys}, "request_id": context["task"]["request_id"]}


def generate_pr_binding_block(binding):
    return START + "\n" + _canonical(binding).decode("utf-8").rstrip("\n") + "\n" + END


def expected_pr_binding(context, pin, *, repository_root=None):
    """The exact task block available before the candidate commit exists."""
    return generate_pr_binding_block(expected_binding(context, pin, repository_root=repository_root))


def require_pr_binding(body, expected_block):
    try:
        raw = body.encode("utf-8") if isinstance(body, str) else None
    except UnicodeError:
        raw = None
    if raw is None or len(raw) > 65536 or "\x00" in body:
        raise Blocked("invalid_pr_body")
    # Also reject a malformed second marker, not just a second valid block.
    if (body.count("<!-- msw-coding-task-binding:") != 2
            or body.count(START) != 1 or body.count(END) != 1):
        raise Blocked("ambiguous_pr_binding")
    start, end = body.index(START), body.index(END) + len(END)
    if (body[start:end] != expected_block or (start and body[start - 1] != "\n")
            or (end < len(body) and body[end] != "\n")):
        raise Blocked("pr_binding_mismatch")
    return hashlib.sha256(raw).hexdigest()


def _policy(repository_root=None):
    root = task_context.ROOT if repository_root is None else Path(repository_root)
    try:
        return controller.load_policy(root / "msw-github-engine/policy.json")
    except controller.Blocked:
        raise Blocked("invalid_target_policy") from None


def _path(path, context, policy):
    if not isinstance(path, str) or not path.startswith(tuple(context["allowed_paths"])):
        raise Blocked("task_scope_mismatch")
    # PurePosixPath normalizes // and .; reject those aliases before using it.
    if str(PurePosixPath(path)) != path:
        raise Blocked("unsafe_path")
    try:
        controller.checked_path(path, policy)
    except controller.Blocked as exc:
        raise Blocked(str(exc)) from None


def _git(root, *args, binary=False):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
           "LC_ALL": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1"}
    try:
        value = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                                "-c", "protocol.allow=never", "-C", str(root), *args],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                               check=False, env=env)
        if value.returncode or len(value.stdout) > 33554432:
            raise Blocked("git_read_failed")
        return value.stdout if binary else value.stdout.decode("utf-8").strip()
    except (OSError, UnicodeError, subprocess.SubprocessError):
        raise Blocked("git_read_failed") from None


def _tree(root, commit):
    result = {}
    for item in _git(root, "ls-tree", "-rz", "--full-tree", commit, binary=True).split(b"\0"):
        if item:
            try:
                metadata, path = item.split(b"\t", 1)
                mode, kind, sha = metadata.decode("ascii").split()
                result[path.decode("utf-8")] = (mode, kind, sha)
            except (ValueError, UnicodeError):
                raise Blocked("invalid_git_tree") from None
    return result


def _clean(root, current):
    options = ("--no-ext-diff", "--no-textconv", "--name-only", "--no-renames", "-z")
    if (_git(root, "diff", *options, "HEAD", "--", binary=True)
            or _git(root, "diff", "--cached", *options, "HEAD", "--", binary=True)):
        raise Blocked("candidate_not_clean")
    for raw in _git(root, "ls-files", "--others", "--exclude-standard", "-z", binary=True).split(b"\0"):
        if raw:
            try:
                path = raw.decode("utf-8")
            except UnicodeError:
                raise Blocked("candidate_not_clean") from None
            if (path in current or not (path.startswith(controller.REPORT_DIR + "/")
                                        or "__pycache__" in PurePosixPath(path).parts)):
                raise Blocked("candidate_not_clean")


def _local_changes(root, base, commit, context, policy):
    old, current = _tree(root, base), _tree(root, commit)
    _clean(root, current)
    paths = sorted(path for path in old.keys() | current.keys() if old.get(path) != current.get(path))
    if not 1 <= len(paths) <= policy["max_files"]:
        raise Blocked("invalid_change_count")
    changes, size = [], 0
    for path in paths:
        _path(path, context, policy)
        if path in old and controller.is_test(path):
            raise Blocked("existing_test_changed_or_deleted")
        for entry in (old.get(path), current.get(path)):
            if entry and (entry[0] not in {"100644", "100755"} or entry[1] != "blob"):
                raise Blocked("gitlink_or_link_forbidden")
        entry = current.get(path, old.get(path))
        blob_size = int(_git(root, "cat-file", "-s", entry[2]))
        size += blob_size
        if size > policy["max_bytes"]:
            raise Blocked("byte_limit")
        raw = _git(root, "cat-file", "blob", entry[2], binary=True)
        change = "deleted" if path not in current else "modified" if path in old else "added"
        if change != "deleted":
            try:
                text = raw.decode("utf-8")
            except UnicodeError:
                raise Blocked("binary_file_forbidden") from None
            if "\x00" in text or controller.SECRET_RE.search(text):
                raise Blocked("secret_or_binary_content")
            local = Path(root) / path
            try:
                if any((Path(root) / Path(*PurePosixPath(path).parts[:i])).is_symlink()
                       for i in range(1, len(PurePosixPath(path).parts) + 1)):
                    raise Blocked("symlink_forbidden")
                with open(local, "rb", opener=lambda p, f: os.open(p, f | os.O_NOFOLLOW)) as stream:
                    info = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != blob_size
                            or stream.read(blob_size + 1) != raw):
                        raise Blocked("candidate_content_mismatch")
            except OSError:
                raise Blocked("candidate_content_mismatch") from None
        changes.append({"path": path, "change": change, "git_blob_sha": entry[2],
                        "sha256": None if change == "deleted" else hashlib.sha256(raw).hexdigest()})
    return changes


def _test_evidence(changes, tests, *, full):
    try:
        selected = controller.select_checks(changes)
    except controller.Blocked as exc:
        raise Blocked(str(exc)) from None
    if not isinstance(tests, list) or len(tests) != len(selected) or not tests:
        raise Blocked("missing_test_evidence")
    output, seen = [], set()
    for test in tests:
        if not isinstance(test, dict) or not isinstance(test.get("id"), str):
            raise Blocked("invalid_test_evidence")
        name = test["id"]
        if name in seen or name not in selected:
            raise Blocked("unexpected_test_evidence")
        seen.add(name)
        expected = selected[name]
        if (type(test.get("checks")) is not int or test["checks"] < expected["minimum"]
                or test["checks"] > 1000000):
            raise Blocked("insufficient_test_evidence")
        _sha(test.get("output_sha256"), length=64, code="invalid_test_digest")
        if full:
            required = {"id", "command", "cwd", "status", "exit_code", "checks", "output_sha256", "duration_seconds"}
            duration = test.get("duration_seconds")
            if (set(test) != required or test["status"] != "PASS"
                    or type(test["exit_code"]) is not int or test["exit_code"] != 0
                    or test["command"] != expected["command"] or test["cwd"] != expected["cwd"]
                    or type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0):
                raise Blocked("invalid_test_evidence")
        elif set(test) != {"id", "checks", "output_sha256"}:
            raise Blocked("invalid_test_evidence")
        output.append({key: test[key] for key in ("id", "checks", "output_sha256")})
    return sorted(output, key=lambda item: item["id"])


def bind_result(context, pin, report, proof, *, root, repository_root=None, report_bytes=None):
    """Read local Git state and bind the trusted controller's successful report."""
    binding = expected_binding(context, pin, repository_root=repository_root)
    policy = _policy(repository_root)
    if (not isinstance(proof, dict) or set(proof) != {"status", "base_sha", "commit", "tree"}
            or proof.get("status") != "PASS" or proof.get("base_sha") != context["base_sha"]):
        raise Blocked("invalid_candidate_proof")
    commit = _sha(proof["commit"], code="invalid_candidate_commit")
    tree = _sha(proof["tree"], code="invalid_candidate_tree")
    branch = "msw-coding-task/" + context["task_id"]
    if (_git(root, "rev-parse", "HEAD") != commit or _git(root, "rev-parse", "HEAD^{tree}") != tree
            or _git(root, "show", "-s", "--format=%P", "HEAD") != context["base_sha"]):
        raise Blocked("candidate_commit_mismatch")
    if _git(root, "symbolic-ref", "--short", "HEAD") != branch:
        raise Blocked("candidate_branch_mismatch")
    changes = _local_changes(root, context["base_sha"], commit, context, policy)
    plain_changes = [{key: item[key] for key in ("path", "change", "sha256")} for item in changes]
    if (not isinstance(report, dict) or set(report) != {"status", "base_sha", "changes", "tests", "production_verified"}
            or report.get("status") != "PASS" or report.get("base_sha") != context["base_sha"]
            or report.get("production_verified") is not False or report.get("changes") != plain_changes):
        raise Blocked("validation_report_mismatch")
    tests = _test_evidence(changes, report["tests"], full=True)
    raw = _canonical(report) if report_bytes is None else report_bytes
    if not isinstance(raw, bytes) or len(raw) > MAX_ARTIFACT or _json(raw) != report:
        raise Blocked("validation_bytes_mismatch")
    # Observe HEAD and worktree again after reading all blobs and evidence.
    if (_git(root, "rev-parse", "HEAD") != commit
            or _git(root, "symbolic-ref", "--short", "HEAD") != branch):
        raise Blocked("candidate_changed_during_binding")
    _clean(root, _tree(root, commit))
    return {"schema": PROOF_SCHEMA, "status": "PASS", "context_sha256": pin,
            "binding": binding, "base_sha": context["base_sha"], "commit": commit, "tree": tree,
            "branch": branch, "changes": changes, "tests": tests,
            "validation_sha256": hashlib.sha256(raw).hexdigest(),
            "pr_binding_block": generate_pr_binding_block(binding),
            **{key: True for key in TRUE_FLAGS}, **{key: False for key in FALSE_FLAGS}}


def validate_proof(context, pin, proof, *, repository_root=None):
    binding = expected_binding(context, pin, repository_root=repository_root)
    policy = _policy(repository_root)
    if (not isinstance(proof, dict) or set(proof) != PROOF_KEYS
            or proof.get("schema") != PROOF_SCHEMA or proof.get("status") != "PASS"
            or proof.get("context_sha256") != pin or proof.get("binding") != binding
            or proof.get("base_sha") != context["base_sha"]
            or proof.get("branch") != "msw-coding-task/" + context["task_id"]
            or proof.get("pr_binding_block") != generate_pr_binding_block(binding)
            or any(proof.get(key) is not True for key in TRUE_FLAGS)
            or any(proof.get(key) is not False for key in FALSE_FLAGS)):
        raise Blocked("invalid_task_proof")
    for key in ("commit", "tree"):
        _sha(proof[key], code="invalid_" + key)
    _sha(proof["validation_sha256"], length=64, code="invalid_validation_digest")
    changes = proof["changes"]
    if not isinstance(changes, list) or not 1 <= len(changes) <= policy["max_files"]:
        raise Blocked("invalid_change_count")
    seen = []
    for item in changes:
        if not isinstance(item, dict) or set(item) != CHANGE_KEYS:
            raise Blocked("invalid_change_evidence")
        _path(item["path"], context, policy)
        if item["path"] in seen or item["change"] not in ("added", "modified", "deleted"):
            raise Blocked("invalid_change_evidence")
        if item["change"] != "added" and controller.is_test(item["path"]):
            raise Blocked("existing_test_changed_or_deleted")
        seen.append(item["path"])
        _sha(item["git_blob_sha"], code="invalid_blob_sha")
        if item["change"] == "deleted":
            if item["sha256"] is not None:
                raise Blocked("invalid_deleted_digest")
        else:
            _sha(item["sha256"], length=64, code="invalid_content_digest")
    if seen != sorted(seen):
        raise Blocked("unordered_change_evidence")
    if _test_evidence(changes, proof["tests"], full=False) != proof["tests"]:
        raise Blocked("unordered_test_evidence")
    return proof


class PullReader:
    """Fixed GitHub GETs, inherited TLS/no redirects/no proxies and size limits."""

    def __init__(self, token, http=None):
        if (not isinstance(token, str) or not 1 <= len(token) <= 16384
                or any(ord(char) <= 32 or ord(char) >= 127 for char in token)):
            raise Blocked("missing_or_invalid_read_token", stage="credentials")
        self._token = token
        self._http = bridge.JsonHTTP() if http is None else http

    def get(self, suffix, stage, kind=dict):
        try:
            status, value = self._http.request("GET", bridge.API_ROOT + suffix, self._token,
                                               stage=stage, max_bytes=MAX_ARTIFACT)
        except bridge.BridgeError as exc:
            raise Blocked("github_read_failed", stage=stage, http_status=exc.status) from None
        if status != 200 or not isinstance(value, kind):
            raise Blocked("invalid_github_response", stage=stage)
        return value


def _pr_identity(pr, number, context, proof):
    if (type(pr.get("number")) is not int or pr["number"] != number or pr.get("state") != "open"
            or pr.get("merged") is not False or pr.get("merged_at") is not None):
        raise Blocked("pr_not_open_proposal", stage="pr")
    for key in ("base", "head"):
        ref = pr.get(key)
        repo = ref.get("repo") if isinstance(ref, dict) else None
        if (not isinstance(repo, dict) or type(repo.get("id")) is not int
                or repo["id"] != REPOSITORY_ID or repo.get("full_name") != context["repository"]):
            raise Blocked("pr_repository_mismatch", stage="pr")
    if pr["base"].get("ref") != context["base_branch"]:
        raise Blocked("pr_base_branch_mismatch", stage="pr")
    if pr["base"].get("sha") != context["base_sha"]:
        raise Blocked("stale_pr_base_rebase_retest_required", stage="pr")
    if pr["head"].get("ref") != proof["branch"] or pr["head"].get("sha") != proof["commit"]:
        raise Blocked("pr_head_mismatch", stage="pr")
    if (type(pr.get("changed_files")) is not int or not 1 <= pr["changed_files"] <= 12):
        raise Blocked("invalid_pr_change_count", stage="pr")
    body_sha = require_pr_binding(pr.get("body"), proof["pr_binding_block"])
    return {"body_sha256": body_sha, "changed_files": pr["changed_files"]}


def _remote_files(files, proof):
    expected = {item["path"]: item for item in proof["changes"]}
    seen = set()
    statuses = {"added": "added", "modified": "modified", "removed": "deleted"}
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
            raise Blocked("invalid_pr_file", stage="pr_files")
        path = item["filename"]
        wanted = expected.get(path)
        status = item.get("status")
        if wanted is None or path in seen or item.get("sha") != wanted["git_blob_sha"]:
            raise Blocked("pr_file_mismatch", stage="pr_files")
        if status == "renamed":
            previous = item.get("previous_filename")
            before = expected.get(previous) if isinstance(previous, str) else None
            if (before is None or previous in seen or before["change"] != "deleted"
                    or wanted["change"] != "added"):
                raise Blocked("pr_rename_mismatch", stage="pr_files")
            seen.add(previous)
        elif statuses.get(status) != wanted["change"] or "previous_filename" in item:
            raise Blocked("pr_file_status_mismatch", stage="pr_files")
        seen.add(path)
    if seen != set(expected):
        raise Blocked("pr_file_set_mismatch", stage="pr_files")


def _remote_blobs(reader, proof, policy):
    size = 0
    for item in proof["changes"]:
        sha = item["git_blob_sha"]
        blob = reader.get("/git/blobs/" + sha, "pr_blob")
        length = blob.get("size")
        if (blob.get("sha") != sha or blob.get("encoding") != "base64"
                or type(length) is not int or not 0 <= length <= policy["max_bytes"]
                or not isinstance(blob.get("content"), str)
                or len(blob["content"]) > policy["max_bytes"] * 2):
            raise Blocked("invalid_pr_blob", stage="pr_blob")
        size += length
        if size > policy["max_bytes"]:
            raise Blocked("byte_limit", stage="pr_blob")
        try:
            raw = base64.b64decode(blob["content"].replace("\r", "").replace("\n", ""), validate=True)
        except (ValueError, UnicodeError):
            raise Blocked("invalid_pr_blob_encoding", stage="pr_blob") from None
        actual_sha = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
        if (len(raw) != length or not hmac.compare_digest(actual_sha, sha)
                or (item["change"] != "deleted"
                    and not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), item["sha256"]))):
            raise Blocked("pr_blob_digest_mismatch", stage="pr_blob")


def verify_pr(context, pin, proof, pr_number, *, token, http=None, repository_root=None):
    """Observe a real, still-open PR; return proposal evidence, never execution authority."""
    validate_proof(context, pin, proof, repository_root=repository_root)
    if type(pr_number) is not int or not 1 <= pr_number <= 2147483647:
        raise Blocked("invalid_pr_number")
    reader = PullReader(token, http=http)
    suffix = "/pulls/" + str(pr_number)
    pr = reader.get(suffix, "pr")
    identity = _pr_identity(pr, pr_number, context, proof)
    commit = reader.get("/git/commits/" + proof["commit"], "pr_commit")
    parents, tree = commit.get("parents"), commit.get("tree")
    if (commit.get("sha") != proof["commit"] or not isinstance(tree, dict) or tree.get("sha") != proof["tree"]
            or not isinstance(parents, list) or len(parents) != 1 or not isinstance(parents[0], dict)
            or parents[0].get("sha") != context["base_sha"]):
        raise Blocked("pr_commit_mismatch", stage="pr_commit")
    files = reader.get(suffix + "/files?per_page=100&page=1", "pr_files", kind=list)
    if len(files) != identity["changed_files"]:
        raise Blocked("pr_file_count_mismatch", stage="pr_files")
    _remote_files(files, proof)
    _remote_blobs(reader, proof, _policy(repository_root))
    observed_again = _pr_identity(reader.get(suffix, "pr_recheck"), pr_number, context, proof)
    if observed_again != identity:
        raise Blocked("pr_changed_during_verification", stage="pr_recheck")
    return {"schema": RESULT_SCHEMA, "status": "PROPOSED_VERIFIED", "context_sha256": pin,
            "binding": proof["binding"], "commit": proof["commit"], "tree": proof["tree"],
            "branch": proof["branch"], "changes": proof["changes"], "tests": proof["tests"],
            "pr_number": pr_number, "pr_url": "https://github.com/" + context["repository"] + "/pull/" + str(pr_number),
            "pr_body_sha256": identity["body_sha256"], "proof_sha256": hashlib.sha256(_canonical(proof)).hexdigest(),
            **{key: True for key in TRUE_FLAGS}, **{key: False for key in FALSE_FLAGS if key != "pr_verified"},
            "pr_verified": True}


def main(argv=None, environ=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    for operation in ("bind", "verify-pr"):
        command = commands.add_parser(operation)
        for name in ("context", "expected-context-sha256", "proof", "output"):
            command.add_argument("--" + name, required=True)
        if operation == "bind":
            command.add_argument("--report", required=True)
            command.add_argument("--root", default=".", help="Candidate Git worktree")
        else:
            command.add_argument("--pr-number", type=int, required=True)
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    try:
        context = load_context(args.context, args.expected_context_sha256)
        proof = _json(_read(args.proof))
        if args.operation == "bind":
            raw = _read(args.report)
            result = bind_result(context, args.expected_context_sha256, _json(raw), proof,
                                 root=Path(args.root), report_bytes=raw)
        else:
            result = verify_pr(context, args.expected_context_sha256, proof, args.pr_number,
                               token=env.get("GITHUB_TOKEN"))
        with open(args.output, "xb") as output:
            output.write(_canonical(result))
        print(_canonical({"schema": result["schema"], "status": result["status"],
                          "task_id": context["task_id"], "commit": result["commit"],
                          "pr_verified": result["pr_verified"], "execution_authorized": False,
                          "production_hold": True}).decode("utf-8"), end="")
        return 0
    except (Blocked, OSError, ValueError, TypeError, UnicodeError) as exc:
        reason = exc.code if isinstance(exc, Blocked) else "result_io_or_format_error"
        print(_canonical({"schema": RESULT_SCHEMA, "status": "BLOCKED", "reason": reason,
                          "execution_authorized": False, "production_hold": True}).decode("utf-8"), end="")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
