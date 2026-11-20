# Hermes native Codex entry v1

An isolated, manual entry for **new owner text tasks** using the installed Hermes
`CodexAppServerSession` and the existing ChatGPT account. It does not admit existing
business queue jobs or change the active `msw-auto` adapter, global Hermes profile,
workers, ports, approval policy, or credentials.

## Request and invocation

Write a UTF-8 JSON request owned by the runtime user with mode `0600`:

```json
{
  "schema": "msw.hermes.codex-native.request.v1",
  "idempotency_key": "owner-example-unique-key",
  "prompt": "Return a short answer to this new owner text task.",
  "token_limit_policy": "provider_default_no_hard_cap",
  "origin": "new_owner_task"
}
```

All five fields are required; other fields are refused. Keys must start with an
ASCII letter or digit, contain only ASCII letters, digits, dots, underscores, or
hyphens, and be at most 80 characters. Prompts are limited to 64,000 UTF-8 bytes.
The token policy is explicit: the native transport supplies **no hard output token
cap**. Requests requiring such a cap must use their existing capped interface.

After installation at the private path, run:

```sh
/var/data/msw-hermes/envs/v0.23.0-2bd378ed1b8d/bin/python -I -B /var/data/msw-hermes/native-codex-v1/native_entry.py --run /absolute/private/request.json
/var/data/msw-hermes/envs/v0.23.0-2bd378ed1b8d/bin/python -I -B /var/data/msw-hermes/native-codex-v1/native_entry.py --read owner-example-unique-key
```

Output is one JSON object. Exit status 0 means a verified result or a successfully
read local status; inspect `status`. A refusal exits 2. Prompts, results, and
receipts stay under the private per-key run directory. They are not repository
artifacts and must not be committed. The CLI prints the requested answer, so use a
private terminal or private output file.

## Acceptance and replay protection

The model is fixed to `gpt-6.2-sol`. Before dispatch, the entry checks native
`account/read` for ChatGPT authentication, `model/list` for availability, and
recognized, nonexhausted subscription quota windows. It forces ChatGPT login and
does not configure an API-key fallback. The receipt labels billing
`chatgpt_account`; it makes no zero-cost or credit-consumption guarantee.

The process clears inherited environment credentials before native imports and
again before each Codex client. It preserves the OS account home and uses a
separate Hermes home. The existing Codex home is reused in place. Its configuration
must contain only the five previously reviewed top-level settings for approvals,
credential storage, login method, sandbox, and web search; other settings fail
closed before native startup.

Known shell, unified execution, apps, plugins, hooks, multi-agent, and web-search
features are disabled. The native session uses a read-only permission profile and
denies approval requests. This does not assert that every native built-in is
hidden. Only a completed result with zero observed tool iterations and no tool
items in persisted readback is accepted. This entry is for plain text tasks.

A per-key file lock prevents concurrent dispatch. The canonical full request and
reservation are durably written before any provider work; dispatch intent is
durably written before `run_turn`. After completion, the sending session is closed
and a new native client reads the stored thread. Thread ID, model, turn ID,
completed status, and exact final answer must match. Only then is the `VERIFIED`
receipt atomically written and synced. It includes native IDs, response and receipt
SHA-256 digests, usage, account type, and the explicit token policy.

The same key and same body return the verified local receipt without another
native call. A changed body conflicts. Any reserved, unknown, failed, interrupted,
timed-out, or mismatched attempt requires manual review and **never automatically
redispatches**. Do not delete reservations or use another key to replay uncertain
work. `--read` retrieves local evidence; it does not contact the provider.

## Offline verification

```sh
cd /path/to/hermes_codex_native_v1
python -B -m unittest -v test_native_entry.py
```

Tests inject a synthetic backend and temporary private directories. They cover
contract rejection, durable dispatch ordering, cache reuse, conflicts, unknown
outcomes, incomplete reservations, independent-readback mismatches, observed tool
activity, changed cached model, and concurrent admission. They make no provider
calls. A separate deployment canary is required to validate the installed native
transport and existing account on the target service.
