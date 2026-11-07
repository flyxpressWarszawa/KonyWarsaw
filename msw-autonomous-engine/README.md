# MSW background engine

## Shared MSW AI flow

With the accompanying `wordpress/msw-render-federation` bridge installed, set `MSW_LLM_PROVIDER=wordpress` and `MSW_LLM_MODEL=gpt-5-mini` on the existing worker. Enable `llm.generate` alongside its existing node capabilities. The worker uses the existing signed node credential; OpenAI and Python HUB provider secrets stay on WordPress.

The flow also calls the owner's existing cPanel CPU model through its original HMAC protocol, verifies signed response digests and pinned runtime/weight receipts, requests advisory text from the existing owned Render LLM through its paired WordPress bridge, and reads the Technical Core catalogue health. Unavailable peers are reported explicitly. Native Python offer text is a proposal, not proof that a particular external model generated it; catalogue profiles are not counted as running agents. Local model suggestions remain unreviewed data supplied to the final synthesis. Node keys and code are never exported to Render.

An authenticated `POST /msw-federation/v1/submit` accepts `request_id`, `prompt`, `task` (technical/business/seo/article/review/voice), `max_tokens` (64–3000), and optional `use_knowledge` and `collaborate` booleans. The cooperative flow reads actual WordPress plugin inventory and Python backend health, retrieves public service sources through the existing Python HUB adapter and approved Render memory, and asks the existing OpenAI router for a synthesis. Results have contributor evidence and a content digest; `GET /msw-federation/v1/result/{job_id}` independently verifies the queue result digest. Existing PHP modules can submit through `msw_ai_federation_submit` and consume `msw_ai_federation_result`. Submitting uses an idempotency key and does not synchronously block a customer chat.

The router retains its original API quota, input validation and provider configuration. Publication, private case export, arbitrary code execution and weight training are not enabled by this flow. All installed plugins are inventoried; inventory does not claim that every plugin has already adopted the submission hook, or that every original callback has been migrated.

The authenticated `/msw-federation/v1/connect` action adds only `federation.submit` to the existing Central Engine write capability list, verifies its policy readback, and connects the existing public-catalogue change event. Repeated events use a source digest and the same queue idempotency key. No additional cron is created. `federation.status` and `federation.result` expose the shared state to existing Central Engine workflows; queue submission proof is explicitly distinct from finished inference.

Python worker for the authenticated job queue of MSW Unified Engine on WordPress. Core computation uses the Python standard library. Pinned dependencies support the optional private compatibility adapters.

Core handlers: static SEO measurements, evidence-based ranking, approved-source ingestion and lexical retrieval with SQLite FTS5. Ingestion does not train model weights. Optional local models and legacy adapters require explicitly configured resources. Private source and model weights are not part of this repository.

The worker validates signed HTTPS traffic, maintains a private persistent result journal, checks the WordPress acknowledgement and readback, and bounds job CPU, memory and execution time. It cannot execute code received inside job payloads. Training is disabled unless separately enabled and configured.

## Render configuration

`render.yaml` is a deployment proposal for one background worker in Frankfurt, 2 CPU / 4 GB RAM, with a 10 GB persistent disk. Compute and storage are billed separately from a workspace subscription. Committing the proposal does not create or purchase a service. Confirm the current quote before applying it.

Set the root directory to `msw-autonomous-engine`, build command to `python -m pip install -r requirements.txt && python -m compileall -q msw_engine`, and start command to `python -m msw_engine`. Place the worker in Production of the existing MSW project. The worker has no inbound port or public domain.

Register a scoped node in WordPress, then configure MSW_WP_URL, MSW_TRANSPORT_URL, MSW_NODE and MSW_SECRET in Render secret environment variables. Never commit the node secret, administrator credentials, live environment files, customer data or private journals.

Persistent source memory and receipts live in MSW_DATA_DIR. This directory must survive restarts. SQLite state belongs to one worker; horizontal replicas require a shared storage design. Large-model training requires appropriately sized separate compute.

Before reporting deployment complete, verify a real WordPress task, the matching result digest, the stored acknowledgement, result readback, fresh worker health and data persistence after restart.

## Private compatibility packs

The operator can supply a small ZIP through deployment configuration: `MSW_RUNTIME_PACK_CHUNKS`, sequential base64 chunks `MSW_RUNTIME_PACK_00` onward, and `MSW_RUNTIME_PACK_SHA256`. Keep each chunk at most 32,000 characters. A versioned manifest inventories every file with its SHA-256 and names each approved module. Startup rejects unsafe paths, links, unlisted files, changed digests and oversized archives. Jobs cannot select code or paths. Verified source is installed privately on the persistent disk. An invalid pack leaves the core queue available and disables the new adapters.

Supported typed adapters reuse the owner's original module code: Supreme inventory observations, COMBO aggregate analytics, Engine V3 datasets and receipts, classical Quantum assignment planning, local analytic quantum simulations, and a bounded Fotonika QNN classifier simulated on a CPU. Legacy HTTP receivers run through an in-process request adapter inside the bounded job subprocess; native external HTTP pairing is reported false. No extra public server is opened. QNN training updates a small numerical classifier, with no claim of LLM training, QPU access or demonstrated generalization.

Run the integration suite with `MSW_TEST_PACK` pointing to the private pack and `python -m unittest discover -s tests -v`. Tests skip private adapters when the owner has not supplied their source. Never commit the private pack or production environment values.

A pack can also contain the owner's legacy Malta JSONL source corpus. Its digest, byte count, row schema and row count are checked before an atomic private SQLite import. Cached data is checked and reused after restart. Legacy text remains labelled `legacy_unreviewed` in a separate database. It is available only when a `knowledge.search` job explicitly selects `corpus: legacy_malta`; default approved-source searches remain separate. Restoring this corpus does not verify its factual claims, authorize publication or train model weights.
# MSW Business Hunter handoff and checkpoints

The existing `research.rank` queue accepts the explicit `msw.business-hunter.job.v1`
schema in addition to its original ranking requests. The `ingest` action persists a
private Director evidence record in `business-hunter/research.sqlite3`; `status`
creates an immutable checkpoint manifest with report identities and SHA-256 values.
Receipts are verified through a fresh SQLite connection and replay the same result
for the same request. Conflicting requests, injected code, private URLs, query tokens
and claims of verified demand/revenue are rejected. No job executes generated code.

Worker startup creates and independently reads a checkpoint, logging its digest and
report count. An empty checkpoint explicitly means no Hunter reports are stored yet.
Paid GPT research stays in the existing authenticated WordPress router; this module
needs no additional OpenAI key, paid service, scheduler or compute instance. Existing
publication, source admission and commerce checks still determine actual execution.

## Independent owner agent

The central site's existing worker starts `AutonomousAgent` when the WordPress
provider is configured. It needs no browser, desktop, new service or database.
Five independent lanes review historical sources each minute, catalogue source
records every five minutes, discover active WordPress tools and the live COMBO
roster every ten minutes, inspect V3/V5/V7/COMBO/Python every 15 minutes, and
develop a private business brief and product specification hourly using the
existing paid GPT router. The existing fleet pump, native work queue, catalogue
publisher and FotoAtlas keep their current ownership and execution controls.

Schedules, exact GPT inputs, retries and SHA-256 receipts persist under
`MSW_DATA_DIR/autonomous-agent/`. A restart resumes the same request ID and prompt;
timeouts never cause a new paid request to replace an uncertain one. Process locks
prevent parallel duplicate calls. A failed connection lane cannot stop business
work. Completed receipts are independently read back from SQLite. Generated
proposals remain outside approved RAG and are not claims of verified demand,
publication, revenue or model training. The agent executes fixed signed tools;
it never runs GPT code or commands. Existing publication lanes execute approved
native work, not instructions parsed from the model's prose.

An authorized `runtime.catalog` includes agent heartbeat, job counts and the
latest private brief, so the existing no-argument caller can pass it to the
owner's business workflow. Set `include_director_report: false` to omit the
brief, or `MSW_AUTONOMOUS_AGENT=0` to pause this agent only.
Connection recovery continues through the existing COMBO pump; new credentials,
new paid infrastructure and arbitrary integration grants are not created.

The historical lane walks the existing pinned legacy corpus and owner-imported
source database in bounded UTF-8 fragments. Large records span several batches;
the source cursor advances only after a digest-checked model response and a
separate committed readback. Interrupted paid calls reuse their exact payload
and request identity. Summaries stay private, remain labelled unverified, and
inform subsequent business planning. Historical text never grants tool access.
Only program code is committed; archives, notes and credentials stay on the
existing private disk. This does not provide full ChatGPT account/conversation
access or train model weights. Additional account history requires an owner
export imported through the private source ingestion workflow.

A durable integration continuation tracks each engine's native-read evidence,
pending repairs and transport retries across restarts. Even five successful
reads do not mark every active plugin or satellite autonomous: native execution
receipts remain required. Existing signed COMBO and native queue lanes own
execution; inactive plugins remain inactive and no new Render service is created.

All central transport lanes share a disk-backed outage circuit. Three 429/
503/504 responses or network timeouts within a minute pause requests to that
origin. Cooldown grows to 15 minutes; one request probes recovery, and a dead
probe expires. The circuit never replays a write. Existing lease/ACK recovery
decides whether a timed-out mutation committed. Authorization failures stay
authorization failures. Satellite circuits are independent. This mitigates
request overload; PHP defects and hosting resource limits still need a diagnosed
code or hosting fix. `runtime.catalog.transport_recovery` exposes cooldown state.

### All active WordPress engines and owner continuity (worker 1.7)

The agent's priority is to continue the owner's available work. Startup persists
the current owner mission with a verified RAG readback. Every five minutes it
catalogues up to 300 records from the already installed legacy corpus, validating
each source digest and retaining a resumable cursor. This is source indexing,
not a claim to have semantically reviewed every record or to possess the entire
ChatGPT account history. Business context retrieves approved sources, labelled
legacy sources and available owner packages. Current owner rules take precedence
over historical text. No archive text executes code or grants permissions.

Every ten minutes a WordPress lane discovers the live installed-module catalogue,
uses the existing synchronized cache, retains only active modules in its tool
inventory and rotates up to four registered native diagnostic probes. The
existing shared worker continues executing due native actions through
`module.execute` leases; no second native executor or fleet clock is created.
After a verified server ACK, a private ledger records the actual callback,
module IDs and receipt digest, so discovery and execution remain distinguishable.
Inactive plugins are never activated, updated or probed by this lane. The
business planner receives relevant registered tool descriptions instead of
assuming every plugin supports every operation. Missing adapters remain missing.

A provider request with terminal `msw_brain_uncertain` is retained as blocked;
it is not resubmitted under another identity. Future hourly topics can proceed
instead of all business planning hanging behind that one uncertain outcome.
The catalogue includes WordPress tool coverage and continuity progress. Full
history requires an actual authorized export/source; no account-history access
is inferred from an API key or ChatGPT subscription.



The live COMBO roster supplies all satellite identities. A separate ledger keeps
central receipts apart from satellite receipts, which require the receiver HMAC
readback. A roster entry or accepted cron request alone never proves native work.

Gateway HTTP502 can mean a model-specific terminal error, so it does not trip
the shared origin circuit. HTTP503/504, rate-limit responses and network timeouts
still activate backoff. This keeps provider errors separate from site outages.

## Private owner handoff and ChatGPT export

`MSW_OWNER_CONTEXT_JSON` plus its exact `MSW_OWNER_CONTEXT_SHA256` seed up to 100 owner-provided source records (`msw.owner-context.v1`, records with id/title/text/provenance) into the private owner corpus. Startup independently verifies the committed records and a private source checkpoint. Keep actual context out of this public repository.

After the owner requests an account export and an authentic ready email arrives, place only its official HTTPS download grant in the service secret `MSW_CHATGPT_EXPORT_URL`. No account session or browser credential is used. The learning lane retains the original ZIP privately, imports every text-bearing message from all exported branches into a separate SQLite source, verifies its count/readback, and prioritizes that source. Attachments are retained in the original ZIP but are not claimed semantically reviewed. The same completed grant reuses the imported database after restart. Exports are snapshots, not live account access. Current importer bounds are 512 MiB for the archive and 256 MiB for conversations.json. Unsupported export layouts or authentication requirements fail explicitly.

## Render Doctor and hosting failover

This source describes the candidate code; deployment and production behavior
must be verified separately. It uses the existing worker and persistent disk.
The failover observer probes the fixed central HTTPS origin once per minute,
confirms an outage after three failed observations, and resumes the main queue
only after two successful observations. It records bounded, checksum-verified
incident evidence on the existing private disk. This is not a replica of the
website and cannot recover a provider-wide outage by itself.

The hosting repair bridge invokes only the pre-existing scoped
`hosting_doctor_tick` MCP tool. It requires a separately authorized OAuth grant
with `hosting.read`, `hosting.write`, and `offline_access`, provided securely as
Render secret environment variables `MSW_DOCTOR_BRIDGE_CLIENT_ID`,
`MSW_DOCTOR_BRIDGE_ACCESS_TOKEN`, `MSW_DOCTOR_BRIDGE_REFRESH_TOKEN`, and
`MSW_DOCTOR_BRIDGE_EXPIRES_AT` (Unix seconds). Never commit these credentials.
The rotating grant is stored mode 0600 on the persistent disk. An uncertain
refresh is not replayed because doing so can revoke the grant family; a fresh
authorization is then required. The independent bridge cannot act while the
hosting MCP endpoint itself is unavailable.

Optional direct paid AI advice requires the existing service secrets
`OPENAI_API_KEY` and `MSW_OPENAI_MODEL` (or `MSW_LLM_MODEL`). It receives only
bounded technical observations and relevant local evidence, and its output is
advisory, never an executable instruction. A durable ledger prevents a second
paid request when the first outcome is unknown. The existing small local model
is experimental; no additional model-weight training is claimed.

To allow this worker to cancel its own confirmed stalled builds, configure the
scoped Render management key in `MSW_RENDER_DOCTOR_API_KEY` and set
`MSW_RENDER_DOCTOR_CONTROLLER=render`. Use exactly one Render deployment
controller: disable the WordPress-side Render controller if this one is enabled.
This controller handles only one unchanged `build_in_progress` over three hours,
after three observations across at least ten minutes, an existing live deploy,
an immediate recheck, and cancellation readback. It does not silently replay an
uncertain request or deploy arbitrary commits. It cannot act if this worker is
itself not running. Render's persistent disk prevents zero-downtime redeploys.

The existing autonomous agent and `runtime.catalog` receive the Doctor status
and can retrieve incident cases; retrieved observations are data, not commands.
An independently running Hermes process and its tool contract require their own
verification; this code does not claim a Hermes handshake.
