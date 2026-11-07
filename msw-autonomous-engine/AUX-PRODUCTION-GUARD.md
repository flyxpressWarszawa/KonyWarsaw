# Auxiliary worker: bounded production processing

The existing auxiliary worker continues to accept only `seo.analyze`, a
deterministic computation that can be repeated after an interrupted lease.
There is no durable journal or disk on this service. Do not use it for writes,
paid calls, publishing, `module.execute`, or private memory ingestion.

Before each claim, it reads signed account telemetry from the existing WordPress
endpoint. Missing, stale, future, invalid, or unauthorized data fails closed.
At CPU or RAM at least 70% it stops claiming work; after a restart, a telemetry
gap, or pressure it requires two distinct fresh samples below 60% before
continuing. The existing five-minute quiet start and server Retry-After handling
remain in place. It processes at most two jobs per five-minute cycle, rechecking
telemetry between the jobs; pending acknowledgements are replayed first.

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
requires a controlled Render CLI release and post-deploy checks. Nothing here
adds a language model or durable memory to the auxiliary worker.
