# Hermes -> ordinary ChatGPT -> Hermes: required target and mailbox prototype

## Current status — reviewed 2026-11-14

**BLOCKED: no supported and authorized ordinary-ChatGPT conversation transport has been identified or connected.**

This branch contains a local task-mailbox prototype. It does not send messages into a personal chatgpt.com conversation, start ChatGPT replies, retrieve those replies, or run a live two-agent loop. A GitHub commit, a passing mailbox test, an OAuth login, or an enabled Python handler is not evidence of that connection.

## P0 requirement: the ordinary chat, not a substitute

The intended sequence is:

1. Hermes has a task and initiates a message itself.
2. The message starts a new ordinary ChatGPT conversation or continues a specifically selected existing ordinary conversation.
3. ChatGPT performs the authorized work and produces an actual reply.
4. Hermes receives and verifies the reply, stores its receipt, and chooses the next task from that result.
5. Hermes sends the next message without a human having to paste text, click Send, or run a command for each turn.

The target is ordinary ChatGPT. ChatGPT Work, Workspace Agents, separate API conversations, local Codex threads, periodic polling, and a simulated response are **not equivalent acceptance outcomes**. Do not activate any of them as a silent replacement for this requirement. Existing independent local/free/paid model routes remain outside the scope of this task and must not be disabled.

This supersedes the earlier Work/Workspace deployment proposal in this document. It does not grant a missing platform capability.

## What exists in this branch

- `msw_engine/hermes_chat_bridge.py`: private SQLite mailbox with submit, claim, report, ack and status operations.
- A local handler named `hermes.chat.exchange`, exposed only when `MSW_HERMES_CHAT_LOOP_ENABLED=1`.
- Idempotent task IDs, one outstanding task per conversation key, result hashes, and a maximum of 12 rounds per chain.
- Offline tests of mailbox state transitions. These tests use supplied strings instead of a live ChatGPT reply.
- No deployment or live end-to-end ordinary-chat test is established by this branch.

**Important:** `MSW_HERMES_CHAT_LOOP_ENABLED=1` enables the mailbox handler only. It does not connect ChatGPT and must not be used to claim that the P0 requirement is implemented.

The existing hourly task in an ordinary ChatGPT thread can use the `chatgpt_scheduled` executor once an authenticated connector to this mailbox is available. The scheduled executor requires a conversation key on every claim and receives only tasks explicitly assigned to that executor and key. The `chatgpt_work` label remains the default for legacy callers and pre-existing rows. Neither label enables an executor or grants access to a ChatGPT conversation.

## Mailbox protocol — local prototype only

Schema: `msw.hermes.chat-loop.v1`.

| Operation | Intended caller | Local effect |
| --- | --- | --- |
| submit | Hermes | Store a task with task ID and conversation key |
| claim | Separately authenticated executor | Lease a queued task |
| report | The leased executor | Store result text, outcome and SHA-256 |
| ack | Hermes | Check the stored result digest and acknowledge receipt |
| status | Authorized caller | Read mailbox counts and state |

Storage is `hermes-chat-loop/exchange.sqlite3` under the worker's configured data directory. A `conversation_key` in SQLite is **not** an ordinary ChatGPT conversation ID or access authorization. The prototype creates no inbound ChatGPT endpoint and no external connector.

For the scheduled executor, Hermes adds `"executor": "chatgpt_scheduled"` to submit requests. An authenticated scheduled client claims with `{"schema":"msw.hermes.chat-loop.v1","operation":"claim","executor":"chatgpt_scheduled","conversation_key":"msw-chat-thread-001"}`. The connector must bind that key to the authenticated caller's configured conversation and expose only the operations allowed for its role. Report still requires the lease token from that claim. Hermes uses the resulting digest for ACK and explicitly assigns any follow-up to the same executor.

Follow-up tasks require an acknowledged parent. A leased task is not automatically repeated after an uncertain outcome. These are mailbox protections, not proof that an external operation occurred.

## Platform verification

Official documentation checked on 2026-11-14:

- Sign in with ChatGPT can authorize eligible Responses API inference. Its documented HTTP flow requires `store=false` and `stream=true`; `conversation` is unsupported and the client supplies the required history. This does not provide the required ordinary-chat input/reply transport.
- The Conversations API documents API-side conversation objects used with Responses. Do not treat such an object as an existing personal ChatGPT web conversation.
- The documented external ChatGPT trigger is for published Workspace Agents. It is not this project's accepted target, and its trigger API does not itself return the generated answer.
- Event-triggered tasks documented for Gmail, Slack and GitHub run in Work. A scheduled pull is also not direct initiation of an ordinary chat by Hermes.

No supported endpoint for sending to and reading from a selected ordinary ChatGPT web conversation was found in the interfaces reviewed. This is a scoped verification result, not a claim about all future platform capabilities.

Sources:
- https://developers.openai.com/siwc/quickstart
- https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations
- https://developers.openai.com/api/docs/guides/conversation-state
- https://developers.openai.com/workspace-agents/trigger-runs
- https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt

## Required evidence before claiming completion

1. A documented, authorized mechanism for ordinary personal ChatGPT conversations, including server-side initiation and reply retrieval, available to the account.
2. Confirmed secure authentication for that mechanism, without secrets in GitHub, chat, or logs.
3. An actual Hermes-originated job with an ordinary ChatGPT conversation identifier and an actual generated reply correlated to that job.
4. Durable result storage and an explicit Hermes ACK of that result.
5. A second Hermes task derived from that reply, sent into the same ordinary conversation, with a second verified response.
6. Controlled verification of both new-chat creation and continuation of an explicitly selected existing chat before claiming both modes.

The missing platform transport comes **before** deployment. Do not create more scaffolding, deploy the mailbox, or claim completion merely to make the status green. No browser-session extraction, undocumented private endpoint, or access-control bypass is included in this project.

## Deployment and safety scope

The existing signed WordPress queue and Render runtime must be inspected before a future release. A source commit does not replace the worker's persisted runtime automatically. Keep the prototype disabled in production until the missing transport and authentication are available and a real end-to-end test succeeds.

Goal text is untrusted task data: it cannot grant tools, spend authority, account access, or permission for arbitrary changes. Report blocked or unknown outcomes honestly. Preserve existing MSW services, quotas and independent provider routes.
