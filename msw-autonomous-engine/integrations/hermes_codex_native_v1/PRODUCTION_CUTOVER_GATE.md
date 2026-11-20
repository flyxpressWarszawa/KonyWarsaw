# MSW Hermes → Codex production cutover gate (2026-11-20)

Status: **HOLD — no automatic production cutover authorized by evidence yet**. Owner authorized production cutover; this file documents required technical checks, not a completed deployment.

Existing main Render service: `srv-dbltdb9srm7s73ec0v00`. Existing Codex entry: `integrations/hermes_codex_native_v1/native_entry.py`, deployed independently to `/var/data/msw-hermes/native-codex-v1/`. It accepts only `origin=new_owner_task` and rejects the existing business queue. Do not change that restriction without a tested bridge.

## Admission gate

1. Read current Hermes queue state, ownership, leases and active work keys; never create a new root or duplicate an existing business task.
2. Select only **new, unclaimed, text-only, side-effect-free** tasks for initial canary. Existing `chat_blocked_no_ack` tasks are not eligible for automatic replay.
3. Create a durable one-to-one mapping: existing root/task/request/work key → Codex idempotency key. Persist it atomically before dispatch; fail closed on collisions or unknown states.
4. Existing capped `max_output_tokens` contracts cannot silently be converted to `provider_default_no_hard_cap`. The owner must have a separately verified bounded contract or an explicit approved contract change for each admitted task class.
5. Dispatch via native ChatGPT subscription session; do not substitute an OpenAI API key or paid route. Require verified native receipt, independent thread/read, model/turn identity and SHA-256.
6. Commit the result through the **existing** Hermes native apply endpoint, with its original task identity. Do not synthesize ACK from a Codex receipt or GitHub file.
7. Read back durable Hermes ACK and applied effect; only then mark the original queue item complete. On unknown outcome, HOLD without automatic redispatch or second writer.
8. Canary one eligible task, then a controlled batch with monitoring of duplicate writes, lease ownership, ACK latency, error codes, provider quota and rollback. Keep current router unchanged until E2E is verified.

## Observed blockers at 2026-11-20 15:18 UTC

Render app log: business job `msw-agent-v1:business:498623`, step 2, `chat_blocked_no_ack`, `native_hermes_application_verified=false`. Separate job reported `server_ack_verified=true`, which does not prove this business job succeeded. Hermes registry showed `currentRoute=local-qwen3-0.6b` and `lastError=NO_ELIGIBLE_ROUTE` in router report. No successful E2E Codex→native apply→ACK was observed.

## Rollback and acceptance

Production flag should remain disabled until the canary meets every gate. On errors, stop new Codex admissions, leave previously leased tasks unchanged, preserve immutable evidence and restore the previous route; never delete work keys, receipts, or reservations. Final acceptance requires task ID, request ID, work key, parent, native Codex thread/turn, receipt digest, native Hermes ACK, effect readback and cost/quota evidence.
