# MSW Central Engine — hourly DualMemory scheduler fix

STATUS: TESTED SOURCE CANDIDATE, PRODUCTION HOLD. Do not merge or deploy while STOP/HOLD, signature, or full backup/restore checks are unresolved.

## Pinned live origin
- Connected GoDaddy 507797 agent, verified read: 2026-11-11 01:31:26 UTC.
- File: public_html/mobilny-serwis-warszawa.pl/wp-content/plugins/msw-central-engine/includes/class-msw-ce-own-model.php
- Expected original SHA-256: 2db20bc48c9fcdd15c3ce967ddae15e10a88dd2b4bee373a89961ec217bc5ea5
- Owner class: MSW_BDir_Own_Model_20261105, central engine version 2.5.2.
- Existing callback: MSW_Central_Engine_Core::tick -> seed -> msw_central_engine_seed -> MSW_BDir_Own_Model_20261105::seed. No new cron or queue.

## Root cause
The historic status receipt owner-model-status-v24 is initialized only once. The original source fell back to that obsolete status checkpoint whenever the latest training candidate was rejected. WordPress had 51 completed training receipts with weight changes, one research promotion, and 50 nonpromotions. Fifty jobs selected an old parent (85731b27...) instead of the current accepted research checkpoint (67f9f7c9...).

## Candidate behavior
1. A previous training with no verified receipt blocks new work; it is never replayed with another request ID.
2. Enqueue one signed, idempotent read-only status request each UTC hour, preserving the existing source/actor authorization.
3. Wait for a hash-checked status receipt from the real existing research model; require a valid checkpoint SHA, action=status, and training_performed=false.
4. Queue the original bounded training once per UTC slot with the FRESH current checkpoint as parent. Rejected candidates and immutable baseline status are never used as a replacement for current model state.
5. Read-only status now reports fresh_status; keep all other plugins, model packs, WordPress settings, queue, Hermes and COMBO unchanged.
6. Paired Python candidate refuses stale-parent training in both legacy and approved RAG actions, and preserves idempotent completed receipt replay.

## CI verification
- The PR branch includes the original byte-pinned PHP source candidate and an isolated fake WordPress test harness.
- PHP lint plus 4 isolated scenarios: correct current parent, no duplicate hourly task, prior uncertain result HOLD, malformed status refused.
- Existing RAG/weight-update Python tests plus the complete worker suite run in GitHub Actions without private keys, paid models, or production writes.
- An isolated test is NOT proof of production deployment or business model quality.

## Mandatory release gates (unmet)
1. Fresh full backup of the REAL GoDaddy 507797 WordPress files and database, with a successfully tested restore procedure. An automatic backup of just this PHP file does not satisfy this prerequisite.
2. Verified already-authorized signed Hermes STOP/HOLD epoch and explicit release permission; no guessing, replacing, or creating a signer to bypass a HOLD.
3. Re-read the original live file and verify the exact source SHA-256 above. On drift, stop and rebase.
4. Execute only a scoped, version-checked release with private backup via an authorized adapter after gates pass. Do not touch satellite sites or commerce.
5. Independently verify changed file SHA, PHP syntax, WordPress health, the Business Director status and signed worker status/ACK.
6. Verify the next actual training parent matches the fresh current checkpoint, with no duplicate job or uncertain replay. Roll back from independently verified original bytes on any regression.
7. Independent manually reviewed rights-cleared RAG train/holdout dataset and Polish technical QA benchmark must be available before any new RAG weight-training job. The existing synthetic byte-loss metric is not proof of technician competence.

This artifact contains no credentials, source exports, checkpoint tensors or customer content.
