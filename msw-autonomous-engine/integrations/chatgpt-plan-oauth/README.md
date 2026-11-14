# Hermes — ChatGPT Plan OAuth

STATUS: STAGED ONLY / NO LIVE PRODUCTION DEPLOY / NO USER OAUTH CONSENT YET.

This module offers an optional ChatGPT-plan OAuth path through the official
Sign in with ChatGPT (SIWC) Responses endpoint. It is NOT the web ChatGPT
conversation API, an ordinary OPENAI_API_KEY, or an unlimited free endpoint.

## Conditions
- OpenAI supports this preview for open-source clients and selected private
  clients. Eligibility of the private MSW/Hermes runtime is NOT confirmed.
- Owner must complete official OAuth PKCE and grant scopes
  resource.invoke and chatgpt.tokens.use.direct.
- OAuth enrollment, secure transfer and refresh are NOT implemented by this
  module. Do not place OAuth tokens in GitHub, chat, logs, or WordPress posts.
- Plan usage is subject to account allowances, quotas and permission controls.
  It does not grant access to ChatGPT chats, memories, or other account history.

## Hermes adapter
- chatgpt.plan.status: status/readiness inspection, no inference and no cost.
- chatgpt.plan.generate: advertised only when MSW_CHATGPT_PLAN_ENABLED=1.
  Missing, expired or non-private credentials cause an error, not a fallback.
- MSW_CHATGPT_PLAN_MODEL: explicit model from the authorized account's
  GET /v1/models catalog.
- MSW_CHATGPT_PLAN_OAUTH_FILE: absolute path to 0600 JSON secret containing
  access_token, scope and numeric expires_at; store under private persistent
  Render storage and transfer only via reviewed secrets management.
- The request to https://api.openai.com/v1/responses uses the OAuth token,
  store=false, stream=true, an input array, and waits for response.completed.
- Scoped request receipts prevent duplicate calls after unknown outcomes.
- No automatic purchases, silent retries, paid-key fallback or provider swaps.
- Existing Hermes router, local models, paid routes and budgets are unchanged.

## Before production
1. Verify client eligibility with OpenAI.
2. Build/review an approved SIWC sign-in PKCE, refresh and private token store.
3. Run offline unit tests, staging status inspection and security review.
4. With explicit owner OAuth consent, test account model list and one bounded
   end-to-end inference; verify signed Hermes ACK and durable receipt.
5. Deploy and read back; keep fast disable with MSW_CHATGPT_PLAN_ENABLED=0.

Official references:
https://developers.openai.com/siwc/token-sharing-open-source
https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms
https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations

## Hermes product distinction

OpenAI lists the *NousResearch Hermes Agent* among participating ChatGPT plan-usage apps. In that **specific** upstream product, the owner can run `hermes auth add openai-codex --type oauth` (or choose ChatGPT or Codex Subscription in `hermes model`) and complete the device-code login. Do not assume those commands are installed on the MSW custom Render worker: it uses the `msw_engine` Python runtime and a separate router. The upstream OAuth consent or credentials must not be imported into MSW's custom transport without a reviewed, authorized handoff and permission to use the supported integration.

Official documentation:
- https://learn.chatgpt.com/docs/sign-in-with-chatgpt
- https://hermes-agent.nousresearch.com/docs/integrations/providers
