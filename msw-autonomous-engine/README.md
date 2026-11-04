# MSW background engine

Python worker for the authenticated job queue of MSW Unified Engine on WordPress. The default runtime requires only the Python standard library.

Default handlers: static SEO measurements, evidence-based ranking, approved-source ingestion and lexical retrieval with SQLite FTS5. Ingestion does not train model weights. Optional local model and legacy adapters require explicitly configured resources; no model weights or private legacy sources are downloaded by this worker.

The worker validates signed HTTPS traffic, maintains a private persistent result journal, checks the WordPress acknowledgement and readback, and bounds job CPU, memory and execution time. It cannot execute code received inside job payloads. Training is disabled unless separately enabled and configured.

## Render configuration

`render.yaml` is a deployment proposal for one background worker in Frankfurt, 2 CPU / 4 GB RAM, with a 10 GB persistent disk. Compute and storage are billed separately from a workspace subscription. Committing the proposal does not create or purchase a service. Confirm the current quote before applying it.

Set the root directory to `msw-autonomous-engine`, build command to `python -m compileall -q msw_engine`, and start command to `python -m msw_engine`. Place the worker in Production of the existing MSW project. The worker has no inbound port or public domain.

Register a scoped node in WordPress, then configure MSW_WP_URL, MSW_TRANSPORT_URL, MSW_NODE and MSW_SECRET in Render secret environment variables. Never commit the node secret, administrator credentials, live environment files, customer data or private journals.

Persistent source memory and receipts live in MSW_DATA_DIR. This directory must survive restarts. SQLite state belongs to one worker; horizontal replicas require a shared storage design. Large-model training requires appropriately sized separate compute.

Before reporting deployment complete, verify a real WordPress task, the matching result digest, the stored acknowledgement, result readback, fresh worker health and data persistence after restart.
