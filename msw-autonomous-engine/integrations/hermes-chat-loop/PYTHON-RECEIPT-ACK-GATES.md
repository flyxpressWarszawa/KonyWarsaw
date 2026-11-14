# Hermes ↔ Python: receipt verification and central ACK promotion gates

Checkpoint: 2026-11-14. This document describes a tested, read-only *candidate*. It is not evidence of an active production ChatGPT–Hermes bridge.

## Confirmed at staging

- Two previously completed synthetic jobs: `py10-20261114-seo` and `py10-20261114-rank`.
- Original outputs and fixed GitHub receipts are independently recomputed by `msw_engine.python_receipt_attestor` from the immutable pinned manifest SHA-256 `0614fff2f9cadf2f819ab8bf426bcd6b2f7658df789553a40978499b56199f72`.
- The staging re-attestation emits `bundle_sha256=e4c5bc32e4cd50d67cf88965606813eab996dd805a0be26c05e7260b1781f247`, with no provider calls, WordPress mutation or ACK claim.
- Rejected cases: wrong manifest, missing/extra receipts, duplicate/noncanonical JSON fields, wrong task identity, changed output, false ACK/delivery flags, production identity, paid-call claims, malformed runtime SHA, and forged/stale/replayed HMAC ACK.
- Offline CI: 113/113 tests passed in GitHub Actions run `43643092794`. Tests prove contract logic only.

## Existing production shape

- Central Hermes on Render is `srv-dbltdb9srm7s73ec0v00`; its existing service uses `python /var/data/msw-hermes/deployments/merge09-20261112/entrypoint.py`. This file is NOT the GitHub branch's default startup code.
- The staging worker is `srv-dbnodkbrjlhs739drc5g`; it starts `python -u -m msw_engine.github_queue_staging` and has a separate disk.
- `python.pilot.verify` is exposed in `tasks.handlers()` **only** when `MSW_HERMES_PYTHON_RECEIPT_VERIFY_HANDLER=1` and the normal runtime path actually loads this code. The handler rejects every service ID other than the exact central worker and every job payload other than the fixed read-only schema. Registering it does not enqueue or claim a job.
- The central worker's signed WordPress `claim → complete → status` transport remains the owner of ordinary queue job ACK. A successful `python.pilot.verify` result would ACK **that new verification job**, not retroactively ACK the original two pilot tasks.
- The HMAC ACK schema in `verify_central_ack_signature` requires a secret held only by the central actor, a bound bundle, a fresh nonce and an independent nonce ledger. A matching HMAC in offline tests does not establish the actual actor identity or network delivery. Never mint that ACK from staging or ChatGPT.
- Ordinary personal ChatGPT messages remain a separate, unverified surface: GitHub queue `knowledge/hermes-chat-loop/github-queue.json` had revision 0 and no real chat ACK when last checked. A scheduled task is polling, not an immediate direct-chat API.

## Only safe release sequence

1. Recover and inspect the true central entrypoint on `/var/data` through authorized access; preserve rollback and receipt SQLite before editing. Never rely on redeploying the code branch alone.
2. Show central runtime capabilities and the exact signed queue contract on WordPress with nonmutating status calls. Preserve the production `production_mutation_allowed=false` guard. Do not enable a public anonymous endpoint.
3. Ensure WordPress origin CPU and RAM are below the approved 70% admission threshold, with independent fresh readings. At 21:30 UTC, cPanel account GoDaddy 507797 was measured at 100% CPU quota and 100% RAM quota; do not send new work while high.
4. Integrate the bounded `python.pilot.verify` capability into the actual central worker through a reviewed release with rollback. Keep the feature flag off by default.
5. Submit **one** owner-approved, idempotent, signed verification job through the existing WordPress queue. Require the same job_id/lease/payload SHA in Render, then matching `complete → status` result SHA and readback.
6. Verify the previous two receipts independently, and record separate statuses: original pilot receipt readback, newly signed central verification-job ACK, and (if later implemented) a distinct exact original-task ACK. Never conflate these.
7. Re-run the same stable request_id to prove idempotency; then run a second new request after the first exact ACK. A timeout/unknown completion must be held for inspection, never resubmitted under a new ID.
8. Only after stable live proof, authorize a single nonmutating read on one COMBO satellite and record that satellite's own receiver receipt. Do not infer 41/41 from central status.
9. Resolve the disabled ordinary ChatGPT automation's `requires_user_action` using supported UI/authorization. Do not bypass it or create duplicate recurring tasks.
10. Require signed release evidence, backup/rollback and independent post-deploy readback before marking production autonomy GO.

## Current status and switches

Staging receipt attestation was tested in Render with opt-in `MSW_HERMES_RECEIPT_ATTEST_STAGING=1` and is being turned OFF following the one-shot run. The Python execution pilot remains disabled (`MSW_HERMES_PYTHON_TASK_PILOT=0`). Do not set `MSW_HERMES_PYTHON_RECEIPT_VERIFY_HANDLER=1` on production based only on CI.

**STAGING RECEIPT ATTESTATION: VERIFIED. CENTRAL HERMES RECEIPT ACK: NOT_VERIFIED. FULL AUTONOMY: NO-GO.**

No API tokens, customer records, passwords or raw private messages included.
