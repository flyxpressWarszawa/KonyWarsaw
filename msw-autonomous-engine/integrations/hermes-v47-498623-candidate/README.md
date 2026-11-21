# V47 498623 source-only staging package

**State: SOURCE_ONLY / NOT DEPLOYABLE / NOT ACTIVATED.**

This branch contains an exact, pinned copy of the 17 runtime candidate files and the original V47 source-lock manifest. It does not modify engine code, startup configuration, task routing, queue state, receipts, grants, STOP, provider policy, or the active worker branch. No tests were run for this artifact.

## Provenance

- Source: `flyxpressWarszawa/signal-harbor-companion`, PR #11, HEAD `a9144fd1c3b01784cb4effb6496a5890f94da588`.
- Candidate source-lock commit: `808b1d804e80ac919998afa009b2efca70ae58de`.
- Source-lock manifest Git blob SHA-1: `69731e957685f85198caad9455f0da187e2d31e6`.
- Runtime source pins: 17/17 matched the manifest at the source HEAD before copying.
- Target base: `flyxpressWarszawa/KonyWarsaw`, worker branch `msw/worker-autostart-final-20261111`, commit `105ad24c1c00ffdcad2857035419afaffa97dcdb`, tree `3baa2df386c037bd34278d6494a92a40991cc1a5`.
- Original Render service release remains outside this branch; this package is not referenced by its startup command.

## Required owner bindings still missing

The target checkout did not contain these APIs when inspected at `105ad24c1c00ffdcad2857035419afaffa97dcdb`: `snapshot_498623`, `assert_498623_fences`, `read_498623_admission`, `read_498623_current_run`, `append_498623_initial_grant`, `read_498623_initial_grant`, and the native jobs-CAS reservation callbacks `reserve_native_request` / `reserve_existing_native_effect`.

The existing target integration points are `msw_engine/__main__.py`, `runner.py`, `tasks.py`, and `business_bridge.py`. They are not modified here. PR #48 is an open draft against the same worker base and also changes `msw_engine/tasks.py` and Hermes chat routing files; ownership of those integration points has not been released.

## Rollback evidence received from Render

The V45/V46 directory existence and private permissions do not establish a safe rollback. Receipt `/var/data/msw-hermes/containment-backups/v46-supplement-ack-reconciliation-ready.json` (SHA-256 f5c3e82a5509f343a8c94c93abddd9d94a8804cf7185c9353a7049efd6e687ce) records `new_database_backups=0`, `database_mutations=0`, and `append_only_ack_reconciliation_required_before_launch=true`. The 2026-11-17 single-ledger backup proof (SHA-256 9e38b30da40c39cb043c2770d11830246085dc4c7f8372ef483c1b4dd73fe826) says `other_databases_included=false`. Neither proves current full backup, isolated restore, or rollback preserving later effects and receipts.

The branch itself can be discarded/reverted without touching production state. That is not a production rollback plan. Do not load, merge, or deploy this package until owner bindings, exclusive writer ownership, complete current backup, isolated restore, and receipt-preserving rollback are independently established.
