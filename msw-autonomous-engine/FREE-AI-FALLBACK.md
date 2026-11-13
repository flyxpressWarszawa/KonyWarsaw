# HERMES: free AI failover adapter (2026-11-13)

**State: tested candidate, not installed or activated on Render/WordPress.** This is an additive, opt-in module, designed for the existing Hermes worker. It does **not** intercept or retry an existing paid OpenAI/Alibaba/Gemini/Groq request. Real deployment requires the owner of the existing worker to expose the module through a bounded, authorized capability after acceptance tests.

## Sources & legality

- `ollama`: own local inference on 127.0.0.1:11434, model configured in `MSW_FREE_OLLAMA_MODEL`; no upstream API account; requires local model weights and CPU/RAM. [Official docs](https://github.com/ollama/ollama/blob/main/docs/api/introduction.mdx).
- `llama_cpp`: own local inference on 127.0.0.1:8080, model configured in `MSW_FREE_LLAMACPP_MODEL`; no upstream account; requires model weights and CPU/RAM. [Official docs](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).
- `ai_horde`: public community text generation with documented anonymous key `0000000000`, queue-based and lower-priority; public, non-sensitive content **only**. [Service](https://horde.aivo.chat/) · [API](https://aihorde.net/api/).

`openrouter/free`, Gemini free-tier, Hugging Face, Cloudflare Workers AI and other account-key services are deliberately **not** implemented as no-registration providers. Paid or key-bearing services must be bound through the existing MSW server-side credential interface; do not copy secrets to this module. Pollinations generation requires a secret key as of 2026-11-01. Bing Search APIs were retired in 2025.

## Design

The unified entrypoints are `Router(data_dir).catalog()`, `submit(request_id,prompt,classification='public'|'internal')`, `poll(request_id)`, and `view(request_id)`. Each request is persisted under `MSW_DATA_DIR/hermes-free-ai/requests.sqlite3` with a deterministic input digest before I/O and is independently read back. Network timeout and ambiguous outcomes stay in `outcome_unknown`, never silently resubmitted. An AI Horde accepted request stays `provider_pending`; use the **same** request ID to poll its status (read-only). No new HTTP listener, cron, paid server, account, API key, customer export or provider identity is created. Requests to crowd AI are barred unless source classification is explicitly public.

Every 20-minute review can re-check allowed providers and circuit health; it does not reset, evade or create vendor quotas. 429 Retry-After delays must be honored even across review cycles. Prefer owned local providers to external crowd service. Do not claim zero compute cost, guaranteed availability, unlimited requests, or model quality equivalence.

## Test and rollout gate

From `msw-autonomous-engine/`, run `python -m unittest tests.test_free_ai_fallback -v`. This suite uses mocks: no live generation or external network. Production integration is **NO-GO** until the existing worker/WordPress signatures, exact source SHA, rollback/backups, resource pressure guard, public input classification and a real no-cost smoke test are verified. When integrating, route **newly admitted** public research tasks before paid provider selection; never move an already paid-pending/unknown request to another identity. 20-minute health checks must use an existing scheduler, with no extra timer/cron.

The user-requested Google/Bing/YouTube/Facebook and 30,000 pages are candidate knowledge sources (respect licenses and site/API terms), not inference providers; search source discovery should run in bounded batches with provenance/URL/updated_at and keep `review_state` separate from `connected_api`.
