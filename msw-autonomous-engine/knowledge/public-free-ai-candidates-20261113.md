# Hermes — public free AI API candidates (2026-11-13)

This file records **public technical sources only**. Every provider remains a candidate. The existence of a public document or model list is **not** proof of production-safe inference, unrestricted commercial use, or unlimited quota. No MSW credentials, private customer data, internal logs, or server addresses are included.

| Provider | Type | Account/key | Free status / constraint | Primary source |
|---|---|---|---|---|
| AI Horde | Volunteer-hosted generation | Anonymous public key `0000000000` (not a secret) | No account for low-priority anonymous requests; availability varies | https://horde-sdk.readthedocs.io/en/stable/haidra-assets/docs/definitions/ |
| Animica | OpenAI-compatible hosted chat | No account/key for `/v1` | Community capacity, ~30 requests/minute/IP stated; check `/v1/models` serving flag; endpoint untested here | https://academy.animica.org/learn/free-inference |
| OpenRouter Free | Hosted AI gateway | **Existing account/API key required** | `openrouter/free` or eligible `:free` models, 20 req/min and 50 req/day at standard free tier | https://openrouter.ai/collections/free-models |
| Google Gemini API | Hosted AI provider | Google API key required | Selected models have free tier; model/account/region-specific quotas | https://ai.google.dev/gemini-api/docs/pricing |
| Pollinations | Hosted image/text generation | `sk_` token for generation | **Not keyless**; free public model listing does not imply free unauthenticated generation | https://gen.pollinations.ai/ |
| Ollama local | Self-hosted inference | No third-party account when local | Host must already run downloaded model; local network only | https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx |
| vLLM local | Self-hosted inference | No third-party account when local | Secure all exposed endpoints, not just `/v1`; GPU resources needed | https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/ |
| Crossref | Research metadata (not AI inference) | Public access | Terms/rate limits apply; no generative model | https://www.crossref.org/documentation/retrieve-metadata/rest-api/ |
| MediaWiki/Wikidata | Research metadata (not AI inference) | Public access | Follow acceptable use, attribution, and request limits | https://www.mediawiki.org/wiki/Wikimedia_APIs |
| Bing legacy Search APIs | **Retired** public search route | n/a | End of service August 11, 2025; never use as AI fallback | https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement |

## Safe fallback release gate

1. Read any existing credential **server-side**, via current MSW resolvers. Never copy token values to code, prompts, GitHub, or external free hosts.
2. For every selected model, verify a fresh live inference, matching model identity, current price, license/ToS, request/minute and request/day caps, and handling of sensitive/private input.
3. Execute at most **one** provider request for one idempotency key. If timeout/receipt is uncertain, **hold**; do not cause a second paid call or duplicate effect.
4. Rotate only on a **confirmed no-effect** quota/budget rejection, not on mere network loss. Observe `Retry-After` and provider-specific cooldown; never rotate user identities, API keys or accounts to bypass provider limits.
5. Once a job enters free-only mode, do not silently return to paid API or buy credits.
6. Public anonymous inference can receive public prompts only. Private MSW/customer content requires explicitly approved private processing, preferably already-running local models.
7. Keep newly discovered providers **disabled by default** until these tests succeed. Store normalized evidence separately from any automatically executable instructions.

This file does not modify the existing MSW Hermes production workflow. The actual private worker executes from its managed disk entrypoint and requires a separate authenticated, reviewed rollout and readback.
