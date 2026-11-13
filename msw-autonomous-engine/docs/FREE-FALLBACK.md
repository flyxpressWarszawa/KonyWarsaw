# Hermes free fallback candidate

Status: **candidate, disabled by default, not deployed or enabled**.

This change adds an opt-in adapter at the existing `federation._finish_request`
boundary. It preserves the signed WordPress queue, Hermes pairing, primary
provider choice and result schema. It does not add another worker, endpoint,
subscription, service, registered account or credential store.

## Verified baseline and deployment gap

The reviewed repository base is
`105ad24c1c00ffdcad2857035419afaffa97dcdb` on
`msw/worker-autostart-final-20261111` in
`flyxpressWarszawa/KonyWarsaw`. The live Render service was observed starting an
existing persistent `merge09-20261112/entrypoint.py` overlay. Its exact source
bytes and manifest have not been reconciled with this repository commit.
Applying this candidate to that overlay is **not authorized by a successful
local test**. No production files, environment variables or secrets were changed.

The observed WordPress native runtime plugin is version 1.0.3. Its signed
`generate` operation permits `openai`, `gemini` and `groq`. It does not permit
an OpenRouter free selector. The `openrouter_wp` adapter therefore checks a
versioned capability and returns `not_sent` against that current interface.
It cannot fall through to the existing, potentially paid OpenRouter model.

## Routing behavior

For an enabled policy, the existing paid primary is attempted once. Its
provider is frozen by the existing federation request cache. The new router
never chooses a second paid provider. For the native runtime transport, the
old Gemini/Groq secondary loop is disabled when the policy path is configured;
their free entitlement has not been established by merely finding a key.

| Observed outcome | Router action |
| --- | --- |
| Verified primary completion | Return the existing answer; do not call free providers. |
| Explicit HTTP 402 / insufficient quota / HTTP 429 rejection | Persist rejection and cooldown, then try the allowed free providers. |
| Explicit HTTP 401 | Record `auth_invalid`, block that provider until verified repair, and try an independently allowed free provider. |
| HTTP 403 or content/policy rejection | Stop. Do not use another model to bypass a provider policy. |
| Timeout, lost response, 5xx, ambiguous transport, pending/invalid answer | Persist `UNKNOWN`; do not reissue or switch provider for that request. |
| Known local circuit refusal before network send | Respect its deadline; another independent allowed free provider may be considered. |
| Missing/expired public input grant | Stop before sending any content to a free provider. |
| Every free provider exhausted, unavailable or locally capped | Return `free_fallback_EXHAUSTED_no_paid_retry`. |

Only the known direct OpenAI/Alibaba terminal-error formats and the native
provider HTTP rejection format are classified. Arbitrary error prose mentioning
"quota" is not proof. In the legacy federation callback, some provider errors
are masked as a generic 502; those remain UNKNOWN. This candidate does not
pretend that the current callback reveals more than it does.

The original direct adapters now append a safe absolute Retry-After deadline
to their existing durable error code. Both delta-seconds and HTTP dates are
supported; a valid long deadline is never shortened. A stored old rejection
that lacks header metadata blocks future calls to that provider until reviewed.
It can still admit a different independent free service for a public input.

The existing runner records a TaskError as a failed result. This change does
not introduce an unverified queue deferral operation. When all providers are
exhausted, the caller receives a visible failure; it must not claim that the
same task has been automatically scheduled for tomorrow. New independent tasks
can use a provider after its proven cooldown/budget reset. Durable UNKNOWN and
completed request identities remain pinned across restarts and policy changes.

## Free transports

### AI Horde anonymous

The implementation uses the exact observed endpoint and model:

- `https://oai.aihorde.net/v1/chat/completions`
- `koboldcpp/Phi-3.5-mini-instruct`
- the documented public anonymous bearer identifier `0000000000`
- a non-streaming, text-only OpenAI-compatible request

A previous owner-authorized smoke request returned HTTP 200 and generated text
with that model. It used 16 output tokens and ended with `finish_reason=length`.
That proves anonymous inference was available at the observation time, not that
long business answers are reliable. The candidate requires `finish_reason=stop`;
truncated, tool-call, altered-model or malformed responses are not accepted as
complete. No additional live generation was performed for this change.

The selected base model's official card identifies an MIT license. The exact
artifact operated by a volunteer worker has not been independently attested.
Treat output as untrusted draft material requiring review. The adapter never
executes tools or model-generated instructions. Capacity and latency are
best-effort. If the model leaves the network, the adapter fails visibly instead
of silently selecting an unreviewed model.

### OpenRouter through existing WordPress credentials

The Python adapter is a capability-gated client for the contract below. The
native PHP extension is **not implemented or deployed in this candidate**.
Current behavior is one signed `status` call and `NOT_SENT`, without generation.
The MSW OpenRouter key remains in the existing WordPress credential resolver;
it is never copied into Render, a repository, a task payload or a chat message.

OpenRouter's existing configured model is not sufficient proof of zero cost.
A future native implementation must explicitly select `openrouter/free` or an
approved `:free` model and impose a hard zero-price request policy. All free
models share the same local account budget; switching models does not reset it.

Jina Reader, Crossref, OpenAlex and other data APIs are not LLM replacements and
are intentionally absent from this text-generation fallback chain.

The existing local COMBO SLM is a separate narrow candidate for design-style
classification. In the reviewed source, `LocalSLM.classify(brief)` returns only
a fixed `style` enum, uses a 2,048-token context and a single CPU thread, and
requires at least 1.5 GiB of free memory. It exposes no general generation
callback. A live log showing six generated tokens therefore does not establish
that it can replace a general Hermes language-model task. This PR neither
downloads weights nor turns that classifier into a general chat endpoint.

## Public input admission

Anonymous workers must never receive customer records, private business context,
credentials, medical information or private files. A label in a web page, task
payload or model output is not an authorization to send those data.

The router requires a trusted local operator grant for the **exact outgoing
text, task and request identity**, computed after any federation enrichment.
It is the SHA-256 of UTF-8 JSON `[request_id, task, text]`, with
`ensure_ascii=False` and separators `(',', ':')`. Output-token budgets are
checked independently by the policy and request fingerprint.

The grant file must be placed by the operator or a separately reviewed trusted
public-research admission component next to the private policy:
`public_grants/<sha256>.json`. No remote task handler writes grants. A missing
grant fails closed. Changing even one character of a retrieved private passage
changes the hash and prevents anonymous transmission. Obvious key, password,
email and telephone patterns are blocked as defense in depth; pattern matching
is not the confidentiality decision.

Grant schema, for review only:

```json
{
  "schema": "msw.public.input.grant.v1",
  "classification": "public_non_customer",
  "values_sha256": "<hash of the exact identity, task and text>",
  "approved_by": "owner",
  "expires_at": 0
}
```

The example is deliberately expired. A real grant requires review of the actual
public input and an explicit expiry. `trusted_public_research_admission` is also
an accepted issuer, but no such producer is activated by this PR. The policy
and grants must be operator-owned regular files without group/world write
permission; symlinks are refused. They belong outside untrusted task/download
directories. Do not commit real grants or private policy files to the repository.

## Budgets and durable state

`MSW_FREE_FALLBACK_POLICY` selects the local policy. Its absence preserves the
existing dispatch for requests that never entered this router. The example
policy has `enabled: false` and creates no runtime configuration by itself.

The example proposes local ceilings of 50 primary calls/day, 25 Horde calls/day
and 50 OpenRouter free calls/day. These are conservative operator caps, **not a
claim about purchased provider entitlements or guaranteed free capacity**.
Output-token ceilings are reserved before sending, and rejected/unknown calls
consume the local attempt budget. No purchase, top-up, new key, account rotation,
IP rotation or provider-limit evasion is implemented. Actual provider limits may
be lower and their rejections take precedence. Other applications may consume
the same provider account quota; this worker's local ledger cannot measure them.

State lives under the existing data directory at
`free-fallback/requests.sqlite3`, in a private directory. SQLite transactions
admit one execution per request, freeze input/policy hashes and reserve budgets
before network activity. Crash reservations remain UNKNOWN. Completed results
are replayed with a checksum. The new ledger stores input hashes and outcome
receipts, not prompt text; answer receipts can still contain sensitive output
from the authorized primary and must remain private. Existing provider ledgers
remain authoritative and intact.

An authentication failure blocks that provider without an automatic reset.
Repair requires evidence that the same authorized account configuration works;
the candidate supplies no "clear all errors" shortcut. Disabling the policy does
not unlock already known UNKNOWN jobs. Do not delete ledgers during rollback.

## Tests and limits of the evidence

Offline tests cover quota -> free -> next free, correct primary success,
UNKNOWN after lost responses or crashes, durable replay, concurrent admission,
401, 403, exact grants, expired grants, sensitive content, daily budgets,
Retry-After dates, origin circuits, current WP capability absence and zero-cost
native receipt validation. Existing federation-resume and Alibaba direct/queue
tests verify that explicit Alibaba jobs still use the requested provider.

Run from `msw-autonomous-engine`:

```sh
python -m unittest discover -s tests -p test_free_fallback.py -v
python -m unittest discover -s tests -p test_federation_resume.py -v
python -m unittest discover -s tests -p test_federation.py -v
python -m unittest discover -s tests -p test_aliyun_direct.py -v
```

All transport calls in these tests are mocked. Passing tests does not establish
live OpenRouter access, a PHP extension, production enablement, public-input
admission automation, model quality or active `merge09` compatibility.

## Required native OpenRouter callback contract

Extend the existing authorized `MSW_Agent_Runtime_Bridge` implementation after
retrieving its exact unredacted source and backing it up. Do not add a public
unauthenticated route or weaken its signature, nonce, node-scope or permission
checks. The minimal integration is an early branch inside the existing private
`generate` handler for `provider=openrouter_free`, plus a status capability.
Keep all current paid-provider branches intact.

The future native branch must:

1. Accept only existing request identity/task/text/token fields, `provider`, and
   `public_input_sha256`; reject all other request fields and arbitrary URLs.
2. Verify the same exact public-input grant on the native trusted side. A signed
   string claiming "public" is insufficient without trusted admission.
3. Resolve the existing `openrouter` credential server-side through the native
   credential resolver. Return NOT_SENT if unavailable; do not export it.
4. Maintain a private atomic per-account budget, cooldown and request journal,
   shared across free models. Reserve before transport; replay completed results;
   an accepted/lost response remains UNKNOWN and must not be resubmitted.
5. Use the fixed `https://openrouter.ai/api/v1/chat/completions` endpoint,
   TLS verification, zero redirects, strict timeout/size limits and plain text.
   Send no tools, plugins, images, web search or billable add-ons.
6. Use an approved `:free` selector or `openrouter/free`. Check the model catalog
   and all relevant pricing fields, then use provider `max_price` with prompt,
   completion and request ceilings of zero and `allow_fallbacks:false`. Never
   send a paid model fallback list. Missing/unknown pricing fails closed.
7. Honor full Retry-After and native account limits. Preserve explicit 401,
   402, 403 and 429 semantics. Generic 5xx/lost responses are UNKNOWN.
8. Require a complete text result, verified model/zero-price receipt and private
   journal readback before reporting success. Do not infer cost from model names
   alone. Do not advertise the capability before these checks exist and pass.

Status capability consumed by the Python adapter:

```json
{
  "free_fallback": {
    "schema": "msw.free.callback.v1",
    "enabled": true,
    "providers": {
      "openrouter_free": {
        "configured": true,
        "cost_ceiling_usd": "0",
        "public_input_grants": true,
        "durable_receipts": true,
        "retry_after": true
      }
    }
  }
}
```

Every generation reply must bind `request_id`, `public_input_sha256` and
`storage_readback_verified:true`. Success includes `state:completed`, text,
model, answer SHA-256, `cost_usd:"0"`, `free_model_verified:true` and
`paid_fallback_allowed:false`. A definite rejection uses
`state:provider_failed`, `accepted:false`, a documented `failure_kind` and
absolute `retry_at`. A refusal before submission uses `provider_not_sent`.
Every other state is UNKNOWN to the client. These are future contract fields,
not a claim that the current WP plugin already implements them.

## Deployment and rollback prerequisites

Before enablement, obtain the active overlay manifest and source hashes; compare
the exact dispatcher and provider adapters with this reviewed base. Preserve
verified backups of the active source, policy and all provider/route ledgers.
Confirm deployment/rollback ownership and that no unrelated production changes
will be overwritten. Run CI on the actual proposed revision and stage a public
synthetic task with a real reviewed grant. Confirm the native/OpenRouter
extension separately before advertising it as usable.

Roll back by stopping new admissions and restoring the verified prior code and
policy after in-flight state is reconciled. Preserve every UNKNOWN/accepted and
completed receipt. Never clear a ledger or issue a fresh identity to hide an
uncertain outcome. The active source mismatch, missing native callback and
absence of a staged readback are current blockers to calling this enabled.

## Primary sources reviewed on 2026-11-13

- AI Horde endpoint and anonymous key: <https://github.com/Haidra-Org/AI-Horde/blob/main/README.md>
- Model license and intended uses: <https://huggingface.co/microsoft/Phi-3.5-mini-instruct>
- OpenRouter free variants and shared limits: <https://openrouter.ai/collections/free-models/>
- Free selector: <https://openrouter.ai/openrouter/free>
- Hard maximum price and provider fallback settings: <https://openrouter.ai/docs/guides/routing/provider-selection>
- Pricing metadata: <https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties>

Provider terms, model availability and quotas can change. Review current
conditions before production enablement; a catalog of many models is not a
catalog of independently available, free, no-login APIs.
