# GitHub file verification for the MSW worker

This candidate adds an opt-in, read-only `github.verify_file` handler to the
existing worker. It does not submit code, create branches, modify GitHub, or
enable a queue capability on WordPress. It is disabled unless the existing
worker receives `MSW_GITHUB_VERIFY_ENABLED=1` in its own configuration. Do not
enable it until the central queue's authenticated capability contract is agreed.

The fixed repository is `flyxpressWarszawa/KonyWarsaw`. This first version reads
only permitted source paths, using the GitHub HTTPS API, bounded responses and no
redirects. A full commit SHA, one path, and the independently supplied expected
SHA-256 are required. The implementation checks the commit identity, each Git
tree entry and file mode, the Git blob hash, and the file's SHA-256. File bytes
and tokens are not included in the result. An optional `MSW_GITHUB_READ_TOKEN`
is read from the worker environment, never from a job; the current public
repository does not require it for an ordinary read, subject to GitHub limits.

Example queue payload (replace the placeholders with real values):

```json
{
  "schema": "msw.github.verify-file.v1",
  "request_id": "example-job-123456",
  "commit_sha": "<40 lowercase hexadecimal characters>",
  "path": "msw-autonomous-engine/example.py",
  "expected_sha256": "<64 lowercase hexadecimal characters>"
}
```

On a match the result includes `github_file_verified`, commit, path and both
hashes. It explicitly sets `tests_verified=false` and `deployed_verified=false`.
This is a file identity check. It does not independently authorize the author or
commit, prove tests, indicate that the commit is on a particular branch, prove a
deployment, or prove that a task was executed. If a path refers to a symlink,
submodule, missing file, truncated tree, oversized or changed content, the
handler fails closed. A GitHub rate limit or transport error is a failure, not an
acknowledgement.

The existing worker journal and WordPress capability grant are responsible for
job identity, retry behavior, and the receiver's result acknowledgement. A local
verification result must not be described as a WordPress ACK until that separate
readback succeeds. This candidate adds no new production endpoint, token, secret,
queue, cron, deployment or user-facing GitHub integration on its own.

The installed WordPress queue's allowlist, read on 2026-11-07, does not currently
include `github.verify_file`. Its claim operation requires the intersection of
the queue's allowed types, the individual node grant, and the worker's advertised
capabilities. Until the queue contract is extended and the chosen node receives
an explicit scoped grant, a real claim cannot succeed. Adding the new type must
not silently give it to newly registered nodes: the current registration flow
uses the default type list for non-free nodes. Do not hide this operation inside
another already-granted capability.

Run the focused tests in the worker checkout:

```sh
python -m unittest discover -s tests -p 'test_github_verify.py' -v
python -m unittest discover -s tests -p 'test_github_worker_integration.py' -v
```

The worker integration tests use the actual spawn-based child process, job
payload digest, SQLite journal, and completion/readback code. GitHub HTTP and
the central queue are simulated. They cover successful verification, a failed
file digest, a changed payload, disabled capability, a mismatched server
readback, and reuse of a previously recorded result. Passing them does not
establish that a production queue accepts or authorizes this capability. A
separate test checks that a simulated queue stays idle when the kind is missing
from the allowlist or from the node's grant.

## Coding bridge delivery adapter

The follow-up module `msw_engine/coding_verify.py` connects this verifier to the
proposed coding transport at `msw-coding-bridge/` on the main development line.
It registers `coding.verify_delivery` alongside `github.verify_file` under the
same `MSW_GITHUB_VERIFY_ENABLED=1` opt-in. Both remain absent when the switch is
unset and in `render-free` mode. This is proposed code, not a production grant.

The adapter accepts only `msw-coding-tasks/<32 lowercase hex>.json` for a fixed
repository and the fixed `msw/coding-queue` branch. It first reuses the commit,
tree, blob and file hash checks from PR #3, with a smaller 16 KiB limit. It then
validates the exact `msw.coding-task.v1` schema, canonical UTF-8 bytes and trailing
LF, public-data flag, task identity and internal request digest. Polish text,
emoji and U+2028/U+2029 use the same golden fixture as the PHP/Python transport.
Credential patterns are rejected; this check cannot detect every kind of
private content, so the transport is restricted to public requirements.

There are two different SHA-256 values:

| Field | Bytes covered |
| --- | --- |
| `file_sha256` | The whole canonical task file, including `request_sha256` and its final LF |
| `request_sha256` | Canonical task object with the `request_sha256` field omitted |

`verification_request(transport_receipt, request_id)` maps a `delivered` or
`stored_unacknowledged` transport receipt into the bounded verification payload.
It performs no network call and does not enqueue or authorize a job. Its output
has schema `msw.coding-verify-delivery.v1`, a separate verifier `request_id`, and
`repository`, `queue_branch`, `task_id`, `request_sha256`, `commit_sha`,
`task_path`, and `file_sha256`. A receipt without the file hash is rejected;
the internal request hash must never be substituted for it.

After validating the file and task, the handler obtains the current queue ref.
If the supplied commit is not that head, it checks that the commit is an ancestor
using the GitHub compare API with exact commit IDs. Diverged, unrelated, missing
or malformed branch evidence fails closed. The result records `queue_head_sha`
and `verified_at` so this observation is tied to the head actually read. A replay
of the same native job reuses its immutable journal result; it does not claim a
new observation. A fresh independent observation needs a newly admitted request.

The result separately sets `github_file_verified`, `task_identity_verified`,
`canonical_task_verified` and `queue_branch_membership_verified`. It never sets
`wordpress_ack_verified`, `tests_verified`, `code_executed`, or
`deployed_verified` to true. A source-engine label is not an authenticated
identity. GitHub file identity does not prove a model ran or the request was
implemented.

The existing native worker supplies payload-hash checks, bounded child process,
heartbeat, SQLite journal and complete/status readback. Its currently observed
WordPress RemoteStore kind list does not admit `coding.verify_delivery` or
`github.verify_file`. Adding handlers to the worker does **not** add that grant.
The server-side capability admission and shared STOP/HOLD contract must be
reviewed and agreed before activation; this patch does not route around them
through `module.execute`, change the native kind list, or introduce a second
queue. The production hold on WordPress and Render remains in effect.

Run the additional focused contracts from the worker checkout:

```sh
python -m unittest discover -s tests -p 'test_coding_verify.py' -v
```

These tests include the shared Unicode bytes, both digests, immutable file and
branch evidence, rejected task substitutions, strict paths and default-off
registration. Two tests exercise the new handler through the actual spawned
worker and SQLite journal. Their GitHub and queue ports are explicitly synthetic;
they are not a live WordPress ACK, model run or deployment.
