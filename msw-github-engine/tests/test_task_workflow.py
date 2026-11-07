"""Source/compiled workflow gates and the actual preparation shell integration."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

from test_task_context import FIXTURE, QUEUE_COMMIT, TASK, TOKEN, FakeHTTP, c


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/msw-coding-task.md"
LOCK = ROOT / ".github/workflows/msw-coding-task.lock.yml"
GATED_JOBS = ("activation", "agent", "detection", "safe_outputs", "conclusion", "readback")
PUBLICATION_JOBS = ("safe_outputs", "conclusion", "readback")
EXECUTION_CHECK = "needs.coding_task.outputs.execution_permitted == 'true'"
PUBLICATION_CHECK = "needs.coding_task.outputs.publication_permitted == 'true'"


class WorkflowLoader(yaml.SafeLoader):
    """GitHub uses YAML 1.2; do not interpret its `on` key as YAML 1.1 True."""


WorkflowLoader.yaml_implicit_resolvers = copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
for first, resolvers in WorkflowLoader.yaml_implicit_resolvers.items():
    WorkflowLoader.yaml_implicit_resolvers[first] = [item for item in resolvers if item[0] != "tag:yaml.org,2002:bool"]
WorkflowLoader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


def documents():
    source = WORKFLOW.read_text(encoding="utf-8")
    parts = source.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        raise AssertionError("Workflow must have YAML frontmatter")
    return yaml.load(parts[1], Loader=WorkflowLoader), parts[2], yaml.load(LOCK.read_text(), Loader=WorkflowLoader)


def needs(job):
    value = job.get("needs", [])
    return {value} if isinstance(value, str) else set(value)


def prepare_step(config):
    return next(step for step in config["jobs"]["coding_task"]["steps"] if step.get("id") == "prepare")


def permits(expression, execution="true", publication="true"):
    """Evaluate only the boolean subset present in these real job conditions.

    All unrelated prerequisites are satisfied. Fail on unknown syntax instead
    of assuming an unrecognized compiler expression is a permission check.
    """
    values = {
        "vars.MSW_CODING_TASK_EXECUTOR_ENABLED": "true",
        "github.repository": c.bridge.REPOSITORY, "github.ref": "refs/heads/main",
        "needs.coding_task.result": "success",
        "needs.coding_task.outputs.execution_permitted": execution,
        "needs.coding_task.outputs.publication_permitted": publication,
        "needs.agent.result": "success", "needs.detection.result": "success",
        "needs.detection.outputs.detection_success": "true", "needs.safe_outputs.result": "success",
        "needs.safe_outputs.outputs.created_pr_number": "42",
        "needs.activation.outputs.daily_ai_credits_exceeded": "false",
        "needs.activation.outputs.lockdown_check_failed": "false",
        "needs.activation.outputs.oauth_token_check_failed": "false",
        "needs.activation.outputs.stale_lock_file_failed": "false",
        "needs.activation.outputs.daily_ai_credits_guardrail_status": "ok",
    }
    expression = expression.strip()
    if expression.startswith("${{") and expression.endswith("}}"):
        expression = expression[3:-2].strip()
    expression = re.sub(r"\b(?:needs|vars|github)(?:\.[A-Za-z_][A-Za-z0-9_-]*)+",
                        lambda match: repr(values[match.group()]), expression)
    expression = expression.replace("always()", "True").replace("cancelled()", "False")
    expression = expression.replace("&&", " and ").replace("||", " or ")
    expression = re.sub(r"!(?!=)", " not ", expression).strip()
    tree = ast.parse(expression, mode="eval")
    allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
               ast.Compare, ast.Eq, ast.NotEq, ast.Constant)
    if any(not isinstance(node, allowed) for node in ast.walk(tree)):
        raise AssertionError("Unsupported workflow condition syntax")
    return bool(eval(compile(tree, "<workflow-condition>", "eval"), {"__builtins__": {}}, {}))


def action_outputs(raw):
    """Read the actual runner key/value and multiline output formats strictly."""
    lines = raw.decode("utf-8").split("\n")
    result, index = {}, 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line:
            continue
        if "<<" in line and ("=" not in line or line.index("<<") < line.index("=")):
            key, delimiter = line.split("<<", 1)
            value = []
            while index < len(lines) and lines[index] != delimiter:
                value.append(lines[index])
                index += 1
            if index == len(lines):
                raise AssertionError("Unterminated workflow output")
            index += 1
            value = "\n".join(value)
        else:
            key, value = line.split("=", 1)
        if key in result or not re.fullmatch(r"[a-z_][a-z0-9_]*", key):
            raise AssertionError("Duplicate or invalid workflow output")
        result[key] = value
    return result


class WorkflowStructureTests(unittest.TestCase):
    def setUp(self):
        self.source, self.prompt, self.lock = documents()

    def test_false_gate_blocks_every_model_and_write_job_in_both_documents(self):
        for name, document in (("source", self.source), ("compiled", self.lock)):
            for job_name in GATED_JOBS:
                with self.subTest(document=name, job=job_name):
                    job = document["jobs"][job_name]
                    self.assertIn("coding_task", needs(job))
                    self.assertIn(EXECUTION_CHECK, job["if"])
                    self.assertFalse(permits(job["if"], execution="false", publication="true"))
                    self.assertFalse(permits(job["if"], execution="", publication="true"))
                    self.assertTrue(permits(job["if"]))
                    if job_name in PUBLICATION_JOBS:
                        self.assertIn(PUBLICATION_CHECK, job["if"])
                        self.assertFalse(permits(job["if"], publication="false"))
        for job_name, job in self.lock["jobs"].items():
            permissions = job.get("permissions", {})
            if permissions.get("copilot-requests") == "write":
                self.assertIn(EXECUTION_CHECK, job["if"], job_name)
            if any(value == "write" for key, value in permissions.items() if key != "copilot-requests"):
                self.assertIn(EXECUTION_CHECK, job["if"], job_name)
                self.assertIn(PUBLICATION_CHECK, job["if"], job_name)

    def test_dispatch_inputs_enter_only_quoted_env_arguments_not_shell_interpolation(self):
        inputs = self.source["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"task_id", "queue_commit", "file_sha256"})
        self.assertTrue(all(value["required"] is True and value["type"] == "string" for value in inputs.values()))
        self.assertEqual(set(self.source["on"]), {"workflow_dispatch"})
        expected_env = {"MSW_TASK_ID": "${{ inputs.task_id }}", "MSW_QUEUE_COMMIT": "${{ inputs.queue_commit }}",
                        "MSW_TASK_FILE_SHA256": "${{ inputs.file_sha256 }}"}
        for config in (self.source, self.lock):
            step = prepare_step(config)
            self.assertTrue(all(step["env"].get(key) == value for key, value in expected_env.items()))
            for variable in expected_env:
                self.assertIn('"$' + variable + '"', step["run"])
            for job in config["jobs"].values():
                for item in job.get("steps", []):
                    self.assertNotRegex(item.get("run", ""), r"\$\{\{\s*(?:inputs\.|github\.event\.inputs\.)")
        self.assertEqual(prepare_step(self.source)["run"], prepare_step(self.lock)["run"])

    def test_verified_job_outputs_are_bound_to_actual_pre_model_prompt(self):
        self.assertIn("coding_task", needs(self.lock["jobs"]["activation"]))
        for field in ("prompt_context", "pr_binding", "branch"):
            expression = "${{ needs.coding_task.outputs." + field + " }}"
            self.assertIn(expression, self.prompt)
            self.assertEqual(self.source["jobs"]["coding_task"]["outputs"][field], "${{ steps.prepare.outputs." + field + " }}")
            self.assertEqual(self.lock["jobs"]["coding_task"]["outputs"][field], "${{ steps.prepare.outputs." + field + " }}")
            steps = [step for step in self.lock["jobs"]["activation"]["steps"]
                     if expression in step.get("env", {}).values()]
            self.assertGreaterEqual(len(steps), 2, field)
            self.assertTrue(any("prompt" in step.get("name", "").lower() for step in steps))
        self.assertNotIn("${{ inputs.", self.prompt)

    def test_shared_concurrency_exact_code_checkout_and_readonly_readback(self):
        for config in (self.source, self.lock):
            self.assertEqual(config["concurrency"]["group"], "msw-github-engine")
            self.assertIs(config["concurrency"]["cancel-in-progress"], False)
            self.assertEqual(config["env"]["MSW_ENGINE_BASE_SHA"], "${{ github.sha }}")
            for job_name in ("coding_task", "readback"):
                checkouts = [step for step in config["jobs"][job_name]["steps"]
                             if step.get("uses", "").startswith("actions/checkout@")]
                self.assertTrue(checkouts)
                self.assertTrue(all(step["with"]["ref"] == "${{ github.sha }}" for step in checkouts))
                self.assertTrue(all(step["with"]["persist-credentials"] is False for step in checkouts))
            permissions = config["jobs"]["readback"]["permissions"]
            self.assertTrue(permissions and set(permissions.values()) == {"read"})
        self.assertEqual(self.source["concurrency"]["job-discriminator"], "${{ github.run_id }}")
        self.assertEqual(self.source["checkout"]["ref"], "${{ github.sha }}")
        for job_name in ("agent", "safe_outputs"):
            checkouts = [step for step in self.lock["jobs"][job_name]["steps"]
                         if step.get("uses", "").startswith("actions/checkout@")]
            self.assertTrue(checkouts)
            self.assertTrue(all(step["with"].get("ref") == "${{ github.sha }}" for step in checkouts))
        read = next(step for step in self.lock["jobs"]["readback"]["steps"] if "verify-pr" in step.get("run", ""))
        self.assertEqual(read["env"]["EXPECTED_CONTEXT_SHA256"], "${{ needs.coding_task.outputs.context_sha256 }}")
        self.assertEqual(read["env"]["MSW_PR_NUMBER"], "${{ needs.safe_outputs.outputs.created_pr_number }}")


class WorkflowPreparationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.source, _, self.lock = documents()
        self.temporary = tempfile.TemporaryDirectory(prefix="msw-workflow-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.repo = self.directory / "repo"
        self.repo.mkdir()
        sources = ("msw-github-engine/controller.py", "msw-github-engine/policy.json",
                   "msw-github-engine/task_context.py", "msw-github-engine/task_result.py",
                   "msw-github-engine/execution_gate.py", "msw-coding-bridge/client.py", "msw-coding-bridge/targets.json")
        for name in sources:
            destination = self.repo / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        self.git_env = {"PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                        "GIT_TERMINAL_PROMPT": "0", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        self.git("init", "-q", "-b", "main")
        self.git("add", ".")
        self.git("-c", "user.name=Workflow fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "--no-gpg-sign", "-qm", "Trusted integration fixture")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.runner = self.directory / "runner temp"
        self.runner.mkdir()
        self.output = self.directory / "github-output.txt"
        self.output.write_bytes(b"")
        self.calls = self.directory / "http-calls.jsonl"
        self.marker = self.directory / "forbidden-effect.txt"
        responses = self.directory / "github-responses.json"
        responses.write_text(json.dumps(FakeHTTP().values), encoding="utf-8")
        guard = self.directory / "guard"
        guard.mkdir()
        (guard / "sitecustomize.py").write_text(
            "import json, os, socket, subprocess, urllib.request\n"
            "from pathlib import Path\n"
            "responses = json.loads(Path(os.environ['MSW_TEST_RESPONSES']).read_text())\n"
            "root = 'https://api.github.com/repos/flyxpressWarszawa/KonyWarsaw'\n"
            "def forbidden(*args, **kwargs):\n"
            "    with open(os.environ['MSW_TEST_FORBIDDEN'], 'a') as output: output.write('forbidden\\n')\n"
            "    raise RuntimeError('real network or model process forbidden')\n"
            "class Response:\n"
            "    def __init__(self, value):\n"
            "        self.raw = json.dumps(value).encode()\n"
            "        self.status = 200\n"
            "        self.headers = {'Content-Length': str(len(self.raw))}\n"
            "    def read(self, limit): return self.raw[:limit]\n"
            "    def __enter__(self): return self\n"
            "    def __exit__(self, *args): return False\n"
            "class Opener:\n"
            "    def open(self, request, timeout):\n"
            "        url = request.full_url\n"
            "        if request.get_method() != 'GET' or request.data is not None or not url.startswith(root + '/'):\n"
            "            return forbidden()\n"
            "        if request.get_header('Authorization') != 'Bearer ' + os.environ['GITHUB_TOKEN']:\n"
            "            return forbidden()\n"
            "        suffix = url[len(root):]\n"
            "        if suffix not in responses: return forbidden()\n"
            "        with open(os.environ['MSW_TEST_HTTP_CALLS'], 'a') as output:\n"
            "            output.write(json.dumps({'method': request.get_method(), 'path': suffix}) + '\\n')\n"
            "        return Response(responses[suffix])\n"
            "urllib.request.build_opener = lambda *args, **kwargs: Opener()\n"
            "socket.socket.connect = forbidden\n"
            "socket.socket.connect_ex = forbidden\n"
            "socket.create_connection = forbidden\n"
            "socket.getaddrinfo = forbidden\n"
            "subprocess.Popen = forbidden\n"
            "os.system = forbidden\n"
            "os.execve = forbidden\n",
            encoding="utf-8",
        )
        binaries = self.directory / "bin"
        binaries.mkdir()
        (binaries / "python3").symlink_to(sys.executable)
        self.env = {
            **self.git_env, "PATH": str(binaries) + os.pathsep + os.defpath,
            "PYTHONPATH": str(guard), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1",
            "MSW_TEST_RESPONSES": str(responses), "MSW_TEST_HTTP_CALLS": str(self.calls),
            "MSW_TEST_FORBIDDEN": str(self.marker), "GITHUB_TOKEN": TOKEN,
            "GITHUB_OUTPUT": str(self.output), "RUNNER_TEMP": str(self.runner),
            "MSW_ENGINE_BASE_SHA": self.base, "MSW_TASK_ID": TASK["id"], "MSW_QUEUE_COMMIT": QUEUE_COMMIT,
            "MSW_TASK_FILE_SHA256": hashlib.sha256(FIXTURE).hexdigest(),
            "MSW_CODING_TASK_EXECUTOR_ENABLED": "true", "MSW_EXECUTION_PERMITTED": "true",
            "MSW_PUBLICATION_PERMITTED": "true", "MSW_PRODUCTION_HOLD": "false",
        }

    def git(self, *args):
        return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(self.repo), *args],
                              env=self.git_env, capture_output=True, text=True, check=True, timeout=15).stdout

    def run_prepare(self, **environment):
        # This is the literal compiled step, not a reconstruction of its calls.
        script = prepare_step(self.lock)["run"]
        self.assertEqual(script, prepare_step(self.source)["run"])
        result = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
                                cwd=self.repo, env={**self.env, **environment}, capture_output=True,
                                text=True, encoding="utf-8", timeout=25, check=False)
        self.assertFalse(self.marker.exists(), "Preparation attempted a real network or model operation")
        self.assertNotIn(TOKEN, result.stdout + result.stderr + self.output.read_text())
        return result

    def test_actual_prepare_step_emits_pinned_context_pr_binding_and_closed_outputs(self):
        result = self.run_prepare()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stderr, "")
        directory = self.runner / "msw-coding-task-context"
        self.assertEqual({path.name for path in directory.iterdir()},
                         {"context.json", "context-receipt.json", "execution.json", "pr-binding.txt"})
        raw = (directory / "context.json").read_bytes()
        context = c.bridge.decode_json(raw)
        self.assertEqual(c.context_bytes(context, repository_root=self.repo), raw)
        self.assertEqual(context["base_sha"], self.base)
        self.assertEqual(context["task"], TASK)
        self.assertFalse(context["execution_authorized"])
        outputs = action_outputs(self.output.read_bytes())
        self.assertEqual(set(outputs), {"context_sha256", "prompt_context", "task_id", "target", "branch",
                                       "execution_permitted", "publication_permitted", "reason", "pr_binding"})
        pin = hashlib.sha256(raw).hexdigest()
        self.assertEqual(outputs["context_sha256"], pin)
        self.assertEqual(json.loads((directory / "context-receipt.json").read_bytes())["context_sha256"], pin)
        self.assertEqual(json.loads(outputs["prompt_context"]), context)
        self.assertTrue(outputs["prompt_context"].isascii())
        self.assertNotIn("\n", outputs["prompt_context"])
        self.assertEqual(outputs["execution_permitted"], "false")
        self.assertEqual(outputs["publication_permitted"], "false")
        self.assertEqual(outputs["branch"], "msw-coding-task/" + TASK["id"])
        gate = json.loads((directory / "execution.json").read_bytes())
        self.assertEqual(gate["status"], "HELD")
        self.assertFalse(gate["execution_permitted"])
        self.assertFalse(gate["publication_permitted"])
        self.assertFalse(gate["model_called"])
        self.assertEqual((directory / "pr-binding.txt").read_text(), outputs["pr_binding"] + "\n")
        start, encoded, end = outputs["pr_binding"].split("\n")
        self.assertEqual(start, "<!-- msw-coding-task-binding:start -->")
        self.assertEqual(end, "<!-- msw-coding-task-binding:end -->")
        binding = json.loads(encoded)
        self.assertEqual(binding["schema"], "msw.coding-task-binding.v1")
        self.assertEqual(binding["context_sha256"], pin)
        for key in ("repository", "queue_branch", "queue_commit", "queue_head_sha", "task_path", "task_id",
                    "request_sha256", "file_sha256", "target", "base_branch", "base_sha", "allowed_paths"):
            self.assertEqual(binding[key], context[key])
        self.assertEqual(binding["request_id"], TASK["request_id"])
        events = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(len(events), 6)
        self.assertEqual({event["method"] for event in events}, {"GET"})
        self.assertIn("/compare/", events[-1]["path"])
        for job_name in GATED_JOBS:
            self.assertFalse(permits(self.lock["jobs"][job_name]["if"], outputs["execution_permitted"],
                                     outputs["publication_permitted"]), job_name)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.base)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_wrong_checkout_sha_aborts_before_task_reads_or_outputs(self):
        result = self.run_prepare(MSW_ENGINE_BASE_SHA="f" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.runner / "msw-coding-task-context").exists())
        self.assertEqual(self.output.read_bytes(), b"")

    def test_shell_looking_task_input_is_literal_data_and_cannot_reach_prompt(self):
        injected_file = self.directory / "input-injection-ran"
        value = '$(touch "' + str(injected_file) + '")\nexecution_permitted=true'
        result = self.run_prepare(MSW_TASK_ID=value)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(injected_file.exists())
        self.assertFalse(self.calls.exists())
        self.assertEqual(self.output.read_bytes(), b"")
        directory = self.runner / "msw-coding-task-context"
        self.assertFalse((directory / "context.json").exists())
        self.assertFalse((directory / "execution.json").exists())
        self.assertFalse((directory / "pr-binding.txt").exists())
        self.assertEqual(json.loads((directory / "context-receipt.json").read_bytes())["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
