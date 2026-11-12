# Auxiliary worker: bounded production processing

The auxiliary Render worker accepts only `seo.analyze`, a deterministic task
that can be repeated after lease expiry. Do not use this node for WordPress
writes, paid model calls, publishing, `module.execute`, or private memory ingestion.
The current configured service has a 1 GB persistent disk mounted at
`/tmp/msw-aux-seo`; `MSW_DATA_DIR=/tmp/msw-aux-seo/worker` retains its private
SQLite receipt journal and atomic backoff checkpoint across restarts.

Before each *batch claim*, the worker reads signed account telemetry from the
existing WordPress endpoint. Missing, stale, future, invalid, or unauthorized
data fails closed. At CPU or RAM >=70% it stops claiming; after a restart,
telemetry gap, or pressure, two distinct fresh samples below 60%, within the
90-second freshness window, are required for recovery. The five-minute quiet
start and server Retry-After handling remain. One historical receipt is
attempted for replay per cycle, before new admission.

A healthy cycle makes one signed batch claim for up to four existing SEO jobs.
The whole response (protocol, job IDs, payload digests and 900-second leases)
is validated before any execution. WordPress limits the batch response budget
to 900,000 bytes before reserving leases; the Python transport enforces its
own 1,100,000-byte response ceiling. Each task is independently bounded to
30 seconds and 256 MB, then requires its own signed completion and fresh
server-side status/receipt digest verification. Interrupted or unstarted work
retains existing lease-expiry recovery. A legacy single-job claim remains
supported for compatible rollout/rollback. **The health gate runs once before
batch admission, not between jobs already admitted to the batch.** This is an
explicit request-reduction trade-off: it is not continuous CPU monitoring.

Prerequisite: the separately reviewed `msw-aux-health-scope-once` installer
allows the already registered `msw-render-auxseo-20261107-2032` node with the
existing `seo.analyze` scope to read the same minimal account metrics. The
endpoint still requires a valid signed request, and the existing central-node
authorization remains unchanged. No access to WordPress settings or arbitrary
files is granted. If the endpoint is not installed or authorization fails, the
auxiliary worker stops and reports the failure rather than bypassing the gate.

This is not an operating-system CPU cap and does not cover visitor traffic,
WordPress cron, the main Render service, or work already in flight. `module.execute`
remains a WordPress callback and is not migrated by this change. Deployment
is separate from a Git merge; confirm the deployed commit, startup log, actual
per-job ACKs and origin metrics after any release. A lower *modelled* claim
count is not evidence of lower total HTTP traffic or GoDaddy CPU utilization.
Nothing here adds a language model or private durable memory to the auxiliary worker.
