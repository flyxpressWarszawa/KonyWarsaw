# COMBO SLM 1.0.0 — Render-native factory foundation

The engine runs in Python on the existing Render worker, not in WordPress/PHP.
`msw_engine/combo_slm.py` adds an adapter for actual pretrained Qwen3-0.6B Q8_0 tensor inference, pinned by SHA-256, and durable design/knowledge memory. It is not a model trained from scratch by MSW. Missing model weights never count as successful inference.

`msw_engine/combo_factory.py` adds a private durable queue, one bounded step at a time, candidate generation using the existing `openai_direct` provider, 50 product-guide candidates and 30 three-part article candidates, and a local HTML candidate renderer. Six style families and nine layout variants vary palette, typography, spacing and section order. Reinstalling/updating a domain preserves its current design. Exact palette uniqueness is tested; full visual and semantic uniqueness still requires QA.

## Evidence

44 local unittest checks passed. They include 100 palette registrations, concurrency, persistence, invalid inputs, duplicate guards, stale/false receipts and a complete 50-product/30-article candidate pipeline with explicitly mocked model and writer. These tests do NOT prove production neural inference, actual paid generation or live payment delivery.

The two module blobs read back from GitHub match the locally tested source:
- combo_slm.py: f4812f8e3dcb7ab188d8783c887a8884a804662e
- combo_factory.py: ca88e4cab2d10a1ce8b235d1a2830dc5dc13dc81

Runtime deployment requested: dep-dbnisqu7bikc73cm5iv0, commit 7bdae9a2bd743b68bf13316e8e28266dcb830e86. Last observed state at handoff: build_in_progress, not LIVE. No successful combo_slm_probe log observed yet.

## Runtime configuration

Existing worker: srv-dbltdb9srm7s73ec0v00, Robot workspace, existing 2 CPU/4 GB plan and 10 GB disk. No plan change and no new paid service.

- MSW_COMBO_SLM_ENABLED=1
- MSW_COMBO_SLM_DOWNLOAD=1
- MSW_COMBO_DATA_DIR=/var/data/msw-combo

The optional child process downloads and tests the model independently of the existing worker loop. Parent shutdown terminates the child. There is no new WordPress cron, public endpoint or credential.

## Not yet complete

The new ChatGPT-command/installation-event ingress into the factory queue still needs an authenticated adapter. Domain purchase, hosting allocation, DNS/TLS, COMBO deployment, actual images, PDF delivery, cart/checkout, PayPal/Telegram end-to-end verification, content fact checking and per-satellite repair acknowledgements are not implemented or verified by this new module. These are release requirements, not claims of completed integration. Integrate normal spending policy after 18 November 2026 before enabling unattended paid factory requests; the per-job consent flag is not a full budget policy.

No real set of 50 products or 30 articles was generated or sold by the installation operation. No domain was bought and no new live portal was published. Candidate output remains labelled and noindex until the real production integrations and readbacks pass.
