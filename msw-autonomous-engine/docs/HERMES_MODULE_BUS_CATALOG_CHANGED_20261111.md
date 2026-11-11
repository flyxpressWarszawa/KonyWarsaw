# HERMES-NEXT-OFFLOAD-04 — module_bus_catalog_changed (source-only containment)

**Date:** 2026-11-11. **Production status:** HOLD. **Effect:** tested staging-only admission fix. No change to live WordPress, Render, queue, API credentials, paid providers, cron or callback/recovery.

## 1. Root cause — source evidence from active WordPress

The live file `wp-content/plugins/msw-render-federation/includes/class-msw-module-bus.php`, read via WPVibe, implements:
- `collect()`: catalogs **all installed plugins** and hashes topology `(id, version, active, source_sha256)`; the catalog may change legitimately when plugins are updated, activated or sources change. Current inventory: 330 plugins in the last captured queue receipt.
- `enqueue()`: enqueues `runtime.catalog` with `module_bus.operation='sync'`, the topology SHA and an idempotency key **including the hour** (`module-bus-sync-v1-<SHA>-<YYYYMMDDHH>`). This permits another pending job for an identical topology in a later hour.
- `page(offset)`: recomputes the installed plugin catalog for each 40-row REST page; there is no immutable snapshot across requests.
- `completed()`: refuses to certify an old snapshot if the current catalog SHA/total no longer match. This behavior is correct and must remain.
- **Actual deployed Render central worker** `srv-dbltdb9srm7s73ec0v00`, live commit `581a07e15bafff3f16d21bc2ffb630e576e6aee2`, has `msw_engine/module_bus.py::synchronize()`. It requests pages one at a time and explicitly raises `TaskError('module_bus_catalog_changed')` whenever a page fingerprint differs from the requested fingerprint. This is a **fail-closed checksum guard**, not proof that Render is down.

Authoritative read-only WordPress SQL / option evidence:
- `84` failed **sync** jobs, from `64` distinct historical topology digests. All 84 were queued more than 24 hours before the observation.
- `166` pending sync jobs, from `57` distinct topology digests; **140** were more than 24 hours old.
- `3` completed historical sync jobs. `msw_federation_module_bus_v1.last_sync` is **absent**; the active status reports 166 `pending_receipts` and zero native probes.
- An explicitly inspected failed job `7c8079bd-75a2-bfa3-c249-cae42507548f` was **created 2026-11-06T23:01:36Z**, but marked failed **2026-11-11T21:42:13Z**. Its historical catalog SHA began `e3adc720...`; latest captured queued SHA is `326efa1e...`. It had **one** recorded attempt. No job was retried, claimed or reissued in this investigation.
- This evidence establishes **stale, historical catalog snapshots reaching the worker after the plugin topology changed** as the direct observed reason for that failure. The current code additionally permits mid-pagination drift when a plugin changes during a multi-page read. We did **not** prove which individual plugin triggered a particular SHA change, nor that every failure has the same exact timing.

## 2. One bounded candidate fix (not a deployment)

The new `tools/stage_module_bus_singleflight.py` performs an **exact-source-anchor rewrite** of ONLY the `MSW_Federation_Module_Bus::enqueue()` admission lines, never modifies WordPress in place and never sends a queue request.

Proposed behavior after a future approved installation:
1. Calculate today's current catalog SHA as the existing code does. Inspect only the **last recorded** queued job ID for the **same SHA**.
2. Read the existing queue row by its exact ID; require `kind=runtime.catalog`, `state=pending|running`, exact expected `payload_sha256`. A read error, mismatched job type/hash, terminal job, or changed source **never qualifies for reuse**.
3. Reuse that pending/running job; update only `last_checked` and retain its existing `pending_receipts` binding under the existing WordPress advisory option lock. Output `reused_pending=true` and `completed=false`. **Do not enqueue another hourly copy.**
4. On lock or concurrent option identity conflict, fail closed with `singleflight_state_uncertain`; do not publish a job or claim success.
5. If no identical pending job exists, original enqueue code remains unchanged. No callback code, recipient, plugin activation, machine secrets, SSH, API, cost or production grant is affected.

**Scope:** suppress *future identical enqueues* only. It does **not** expire, delete, requeue, reinterpret or mark old 166 pending jobs; it does not solve mid-pagination snapshot drift. The next separate engineering unit will need a stable, hash-bound multi-page catalog source and a reviewed obsolete-job disposition after a restore test.

## 3. Staging backup, rollback and independent test

GitHub Actions [run #43108202114](https://github.com/flyxpressWarszawa/KonyWarsaw/actions/runs/43108202114) on the exact stage-branch code reported **5/5 Python tests PASS**, including eight isolated PHP execution cases and `php -l` syntax check.

- Staging routine **requires exact expected source SHA-256**, refuses source drift, duplicate anchors, already-modified sources, source symlinks and permissive staging directories.
- Saves a source backup at mode `0600` in a separate private staging directory (mode `0700`), writes a distinct candidate, independently compares file digests to source/candidate.
- Rollback is tested **only against the staging candidate** and read back to exactly the pre-change SHA. Rollback of a candidate modified by someone else fails closed.
- The original source path stays unchanged throughout. No passwords or sensitive source bytes appear in logs. All tests use synthetic fixtures, not WordPress customer content.

**DO NOT treat the isolated backup/rollback test as a production restore test.**

## 4. Production release gates — NO-GO until all pass

1. Collect a **byte-exact unredacted SHA256 of the currently loaded PHP target on the authorized server**; WPVibe read_file redacts patterns and cannot itself attest to those original bytes. The patcher demands exact match and exits on drift.
2. Independently validate a restorable backup of **WordPress files + MariaDB + relevant Render SQLite/disk**, and test actual restore/readback on isolated compatible staging. Capture the immutable rollback artifact path, hash and restore test evidence. No production restore.
3. Resolve global signed STOP/HOLD signer, current epoch, scopes, authorization across Hermes–Render–WordPress. Existing worker request HMAC is **not** equivalent to signed STOP authority.
4. Confirm safe current CPU window (two separate fresh account measurements below 60% for both CPU/RAM), existing active Render service commit and grants; no changes to vendor costs, existing cron schedule, callback/recovery/GAP-5 or native WP Atlas SEO rules.
5. On compatible staging first, compare source, run PHP lint, verify exact queued job ID and result without new paid API, perform an explicit rollback and readback. Only then consider a single controlled production deployment with backed up source, immediate readback and rollback.
6. Track old `module_bus_catalog_changed` jobs by ID but **do not replay ambiguous old jobs or change their states blindly**. Report factual error counts and a future safe policy for obsolete catalog snapshots.

**Current owner-approved task does not bypass the missing STOP/restore safety gates. Production modifications performed in this unit: ZERO.**


## 5. Hardened no-reissue stage — 2026-11-11 22:52 UTC

On the same **stage-only branch** the proposed singleflight admission is now stricter:

- For an identical last queued catalog SHA, the previously reported UUID must resolve to an existing **exact** `runtime.catalog` row and matching encoded `payload_sha256`. If the ID is malformed, the row is missing, the kind or digest differs, or the row is `completed`/`failed`/`uncertain`, **return HOLD / typed error without creating a second job**.
- Only a still `pending`/`running` identical row can be reused. When an existing WordPress state advisory lock succeeds, independently read back the exact option `last_queued`, `last_checked` and `pending_receipts` binding. A failed `update_option` must NOT report success even if the lock returned true.
- If a genuinely different catalog SHA is observed, the original `RemoteStore::enqueue` path is unchanged. A mismatch is not silently reinterpreted as a different job.
- Offline CI on **exact commit `bab37fc5335172ef6a07c1f5610bce52828e7eb7`**, [run #43115376006](https://github.com/flyxpressWarszawa/KonyWarsaw/actions/runs/43115376006): **5/5 tests PASS**, covering **13 synthetic PHP scenarios**, source SHA guard, PHP syntax lint, backup of isolated fixture, independent file readback and staging rollback. The scenario matrix includes terminal states, corrupted digest, missing row, changed catalog, lock race, failed state write and malformed job ID.

### Fresh live readback, NO-GO

On **2026-11-11 22:53 UTC**, WordPress `class-msw-module-bus.php` lines 203–212 STILL contain the old hourly enqueue code and no `MSW_MODULE_BUS_SINGLEFLIGHT_20261111` marker. Live production therefore remains **unchanged**.

The WordPress queue still had **5 pending `runtime.catalog` sync jobs with the same catalog SHA and 5 different job_key values**; no queued record was deleted, retried or mutated. Native resource guard at `2026-11-11T22:53:04Z` reported CPU **100%** and `pressure=true`. Doctor recovery/availability state `site_recovery_verified=false` at last check, independent full restore unproved. No global signed STOP/HOLD epoch acceptance was obtained. A read-only hosting bridge advertises file read/write access but did not return a verifiable SHA256 of the exact production target; **do not guess it**.

**Interpretation:** the real repeated-queue problem is proven and the narrow no-duplicate candidate is strengthened and tested. This did **not** correct the production queue or decrease live CPU. There is **no production deployment authorization** until the preflight gates above pass.
