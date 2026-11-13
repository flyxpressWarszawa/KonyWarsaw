# Hermes ↔ WordPress: Render pure-compute pilot (13 Nov 2026)

## Scope of this candidate

This is an **opt-in** lane of the existing signed WordPress remote queue, not a second Hermes, queue, scheduler, or worker deployment.

- Environment flag: `MSW_RENDER_PURE_OFFLOAD`, default `0` (OFF).
- Only `seo.analyze`, `research.rank` and `knowledge.search` are claimed.
- The existing HMAC-signed `Client.send("claim")` and `Worker.execute/acknowledge` paths preserve lease tokens, local SQLite receipts, result digest and server readback.
- Requests run with a single lane, up to 120 seconds and a 512 MiB child memory bound, with 10 seconds delay after a *verified* receipt, 90 seconds on idle and >=180 seconds after transport errors.
- Does **not** invoke or requeue `module.execute`, `runtime.catalog`, `llm.generate`, `knowledge.ingest`, cron hooks, plugin source changes, paid AI, or customer data.
- WordPress still stores the authoritative job queue. This does **not** drain the 11k+ native `module.execute` backlog automatically.
- The feature is wired only for `https://mobilny-serwis-warszawa.pl` and remains OFF in any other environment.

## Grounded observations (13 Nov, ~11:20 UTC)

- Main Render worker `srv-dbltdb9srm7s73ec0v00`: 2 CPU/4 GB RAM and low observed usage.
- WordPress `ovse_mswue_remote_jobs`: more than 11,500 pending; `module.execute` is overwhelmingly the majority, with stale attempts and native PHP callback execution.
- Origin cPanel 507797: CPU read spiked up to 96%; later dropped to 47%. WordPress once returned HTTP 503 to read-only inspection.
- Native module execution is **not** yet computationally moved to Render. `msw_engine.native_autonomy.execute` reaches the WordPress gateway and its already-pinned, idempotent callback executor.
- The deployed Render start command points to **private versioned disk code** at `/var/data/msw-hermes/deployments/merge09-20261112/entrypoint.py`. A GitHub update alone does not prove that this new module is loaded.

## Mandatory production release gates

1. Confirm actual Hermes launcher, imported module paths and live commit/source pins; do not assume the new runner file is executed merely because the Render service says `live`.
2. Verify complete recoverable database+file backup at origin, plus the Render persistent disk's receipt journals. Record last-good commit and recovery procedure.
3. Verify recent healthy signed claim, completion and identical digest readback. Reconcile `ready_to_complete`, `completion_attempted` and `readback_deferred` BEFORE stopping any worker.
4. Run full relevant Python tests, PHP transport tests, and one bounded isolated test for each of the three permitted job kinds. Check current origin CPU/HTTP 503/429 and only proceed if origin can tolerate extra claims.
5. Release first with flag OFF. With an observed healthy origin, enable only for an observed short canary window; read `pure-offload-lane.json` and server ACK. Never claim a business effect without its own WordPress receipt.
6. Compare queue growth rate, claim latency, CPU/RAM/HTTP 429/503, and result integrity. Revert flag immediately if any origin pressure/circuit breaker or unknown ack appears.

## Rollback & operational limits

- Disable the feature by setting `MSW_RENDER_PURE_OFFLOAD=0` on a **verified, planned restart/deploy**. Follow `OPERATIONS-DRAIN.md`: do not terminate an active task or delete private receipt SQLite files.
- Do not batch-claim legacy native tasks, alter `mswue_remote_jobs` directly, replay stale native execution, or increase workers without a per-capability approval and callback effect test.
- For actual native plugin workload migration: inventory each hook and classify pure calculations vs WordPress side effects; build a signed request/response adapter per pure routine with retained PHP commit/receipt semantics and tested rollback. This is a separate later migration.
- No new cron, services, paid tier, or API keys are required by this candidate.

## Local narrow tests

`python -m unittest tests.test_pure_offload_lane -v` (or `python -m unittest discover -s tests -p 'test_pure_offload_lane.py' -v`): checks capability allowlist, signed queue request shape, refusal of `module.execute`, shutdown, and durable ACK reporting. These isolated tests do not prove live deployment or full integration.
