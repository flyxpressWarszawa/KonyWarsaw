# MSW Hermes <-> ChatGPT — command / receipt loop (staging)

## Status as of 2026-11-14
- Durable **mailbox implemented** and opt-in worker handler staged on this branch.
- **NOT deployed to the active Hermes runtime** on Render. The production worker's
  entrypoint runs from a persisted /var/data deployment; a GitHub commit or PR
  does not change that runtime.
- **NOT connected to the specific interactive chatgpt.com conversation**. A
  remote service cannot insert a user message into that conversation and
  asynchronously cause this assistant to work.
- No unauthorized ChatGPT web scraping, no session cookies, no paid OpenAI calls.
- Existing MSW paid, free, and own-model routes are unaffected.
- After a reviewed deployment, a trusted ChatGPT Work task or approved Workspace
  Agent could be connected as the executor via a separately authenticated
  connector, not merely by setting the flag below.

## Loop contract (already implemented, no network required)

Use the signed existing WordPress queue to dispatch task kind
"hermes.chat.exchange" to the Render MSW worker, after enabling
MSW_HERMES_CHAT_LOOP_ENABLED=1 in the new runtime deployment.

Operation | Origin | Effect
- submit | Hermes director | commit durable task; one outstanding per conversation
- claim | approved ChatGPT executor | lease one task once; return goal and lease token
- report | approved executor | commit result + SHA-256, same-lease idempotence
- ack | Hermes director | confirm result digest and read back result, then unlock next round
- status | either authorized side | states/counts without raw task content

The message schema is msw.hermes.chat-loop.v1.
Jobs are stored on the worker's local private persistent volume under
hermes-chat-loop/exchange.sqlite3. It does not automatically create or
install an external ChatGPT plugin or publish a new HTTP endpoint.

Example payloads, for the signed queue only:
- {"schema":"msw.hermes.chat-loop.v1","operation":"submit","task_id":"msw-job-20261114-0001","conversation_key":"msw-hermes-conv-001","kind":"plan","goal":"Prepare a safe sequence of next steps"}
- {"schema":"msw.hermes.chat-loop.v1","operation":"claim","executor":"chatgpt_work"}
- {"schema":"msw.hermes.chat-loop.v1","operation":"report","task_id":"msw-job-20261114-0001","lease_token":"<one-time-lease-from-claim>","result":"Completed plan","result_sha256":"<sha256-of-UTF8-result>","outcome":"completed"}
- {"schema":"msw.hermes.chat-loop.v1","operation":"ack","task_id":"msw-job-20261114-0001","result_sha256":"<sha256-of-result>"}

Follow-up work is another submit with parent_id equal to the previous,
**acknowledged** task. Maximum 12 turns per chain. No speculative retries
for a leased but uncertain execution; reconcile the external runner first.
Blocked/approval-required results are reported honestly, not rewritten as
completed. No automatic WordPress writes or electrical/security operations.

## Production prerequisites

1. Verify deployed Hermes versions, its canonical data_dir, signed WordPress
   queue adapter and actual source in /var/data before any rollout.
2. Verify a ChatGPT-facing executor:
   * ChatGPT Work event-triggered tasks: on supported GitHub pull-request
     activity in the authorized PRIVATE repo. Runs are in Work, not this chat.
     The owner must configure the trigger under Work -> Scheduled and
     authorize GitHub. Confirm exact activity type and supported writes.
   * Alternatively, Workspace Agent API triggers for eligible Business /
     Enterprise workspaces with a published agent and scoped token. API status
     alone is not the answer; the agent must write the receipt to the approved
     private destination. Not assumed available in a Pro personal account.
   * Optional ChatGPT Plan OAuth adapter in separate PR #47 is NOT an inbound
     ChatGPT chat API and requires approved client credentials and consent.
3. Provide an authenticated, scope-limited tool action linking ChatGPT Work
   to submit/claim/report/ack (or a private GitHub output relay). The backend
   must verify caller authorization and protect lease tokens; do not assume
   that the generic WordPress queue exposes this endpoint to ChatGPT.
4. Repair Hermes' GitHub credential issue only via approved secret manager,
   if GitHub is used as the mailbox. No PAT/token in prompts, logs or GitHub.
5. Run staging with a real task, model reply, signed completion, durable
   readback and Hermes ACK. Verify a second round. Only then consider live.
6. Set the opt-in flag in the deployed environment after all gates pass.
   Rollback: remove the handler flag; do not destroy the persisted ledger.

## Safety boundaries

- The goal text is **untrusted data**: it cannot grant new tools, bypass
  permission scopes, enable purchases, change billing, or run arbitrary code.
- All state transitions are idempotent and backed by SQLite WAL.
- A leased task will NOT be automatically reissued on timeout; its result
  may be unknown. Do not mark "sent" as "done".
- No new cron, public web listener, privileged WordPress rights, paid
  API key, or unstoppable autonomous loop. Preserve all MSW quotas.
- Any high-impact modification needs explicit reviewed permissions and
  authoritative readback. No guarantee of truly simultaneous chats.

Docs:
https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt
https://developers.openai.com/workspace-agents/trigger-runs
https://developers.openai.com/siwc/token-sharing-open-source
