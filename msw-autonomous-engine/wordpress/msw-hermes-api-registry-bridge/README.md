# MSW Hermes API Registry Bridge v0.2.0 — WordPress

This code is **metadata only**, not a general-purpose model inference proxy. It never exports API keys, calls providers, or makes public claims that a catalog item is usable. Registry catalog status is intentionally separate from Hermes runtime READY.

## Authenticated WordPress API

Installed as the single-file WordPress plugin `wp-content/plugins/msw-hermes-api-registry-bridge.php` on `msw-ai.com`.

- `GET /wp-json/msw-hermes-registry/v1/status`: only WordPress administrators can see aggregate state, freshness, and digest.
- `GET /wp-json/msw-hermes-registry/v1/snapshot`: administrator-only detailed metadata snapshot.
- `POST /wp-json/msw-hermes-registry/v1/snapshot`: administrator or dedicated principal with **only** `msw_hermes_registry_write` capability.
- Requires schema `msw.hermes.wp-ai-registry.snapshot.v1`; enforces bounded record counts, monotonic revisions, ACK constraints, UTC timestamp tolerance, unique sanitized IDs, SHA-256 receipt.
- Stores only whitelisted provider/route metadata, not arbitrary input keys, prompts, request content, customer records, API secrets or tokens.
- Only performs local WordPress option updates (`autoload=false`). Does not create cron tasks or provider requests.
- A dedicated least-privilege service principal is **not yet configured**. Do **not** give Hermes an administrator Application Password.

## Evidence (2026-11-14)

Live source readback on WordPress: plugin **activated**, 157 provider/candidate records, 27 Hermes runtime routes, 7 routes previously proved READY by Hermes. WordPress stored digest `5afa1aa7a0404f30c50cdc031563f859f8b3b92b98c41b9d3f43eb2179d7775b` and returned the same digest on a separate read. Invalid schema rejected HTTP 422; stale revision rejected HTTP 409; stored digest unchanged after both negative tests. The existing marketplace summary endpoint continued to respond.

Hermes signed registry audit job `b583a44d-2011-41bb-b7ad-96813bb14116` completed with 157 records checked, providerCalls=0 and paidSpendUsd=0. Its registry-level readyCount=0 is **not a contradiction** of 7 runtime-ready model routes; the two systems have distinct evidence standards. Do not promote registry entries to READY based on a runtime model elsewhere without mapping and a receipt.

The independent main WordPress `msw-chatgpt-router` plugin reports a historical HTTP 429 spend-limit response and has no fresh live test. Its credentials are **not** shared with this plugin.

### Unfinished work

- Deploy a **separately authorized scoped service principal or cryptographic signature** for automatic Hermes → WordPress snapshot delivery. This plugin currently receives **operator-originated** authenticated snapshots.
- Validate user-permitted transport and two sequential job IDs, SHA readback, explicit ACK, idempotency, no paid retries after an unknown outcome.
- Test user-selected inference provider paths one by one, respecting the daily paid cap and provider terms. Provider catalog cardinality is not the number of usable APIs.
- Run clean PHP syntax CI and the post-deploy regression suite. Keep NO-GO for full autonomous inference until evidence exists.

### Rollback

Deactivate `msw-hermes-api-registry-bridge` via WP-CLI or WordPress plugin screen. The plugin adds REST routes only while active; it has no activation-time data mutation. If necessary move the newly created single-file plugin to cPanel Trash. Its metadata option remains unautoloaded and inert after deactivation. The host's older archives were listed; a fresh full site/database restore test was **not** verified.

## Source-of-truth

The detailed 30-point checkpoint and sanitized full catalog are private under `signal-harbor-companion/main/knowledge/hermes-api-registry`. Never copy secret material into public GitHub.

## Confirmed staging Hermes → WordPress round-trip (2026-11-14 20:59 UTC)

- Render isolated worker `srv-dbnodkbrjlhs739drc5g` (feature branch `msw/hermes-chat-loop-20261114`) started release commit `d633e0d47a2f938d1e8e8ae1b28f33f7e2b3131a`.
- A one-shot opt-in path read the private GitHub checkpoint and submitted the whitelisted catalog through `X-MSW-Registry-Token`; only the **SHA-256 of the 256-bit token** resides in the WordPress option. The actual token was passed only to Render staging environment, never to GitHub or chat messages.
- Render log recorded `verified_staging_metadata_only`: registry 119, **157 records**, **27 routes**, **7 runtime READY**. Independent WordPress GET confirmed digest `5e42d032f33aa386852ae4b7b959b6570204b51781c83ca12c81a0a53b9673c9`, revision 119, ACK revision 119, `stale=false`.
- GitHub Actions PHP lint and scoped-auth offline checks: `43640763882` SUCCESS. Staging Python test suite and existing chat-loop tests: `43640141960` and `43640141953` SUCCESS.
- Only **metadata** has been delivered from Hermes staging to WordPress. User-directed provider inference from WordPress, production auto-sync, budget failover and ordinary ChatGPT messages are **not** verified.

### Scoped token handling and rotation

An administrator can issue or rotate the scoped credential through authenticated `POST /wp-json/msw-hermes-registry/v1/token`. **Do not paste the returned token into a conversation, logs or GitHub.** Rotation instantly invalidates the previous token; update the isolated worker's environment with the new value through an authenticated secrets channel, followed by a controlled deploy. The endpoint does not allow token access to the registry's service identity. WordPress enforces a 45-second minimum between service-token writes, and rejects a Hermes heartbeat older than 30 minutes (on service-token writes). Successful authentication grants **only** the read/write of sanitized snapshot endpoints, not post editing, plugin management, model-provider credentials or chat-history access.

The staging publisher has one-shot semantics: no automatic replay of uncertain POSTs. Continuous periodic sync remains out of scope until the live Hermes heartbeat source is wired to a bounded production fetcher.
