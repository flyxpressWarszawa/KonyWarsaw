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

Run the focused tests in the worker checkout:

```sh
python -m unittest discover -s tests -p 'test_github_verify.py' -v
```
