# Hermes ↔ ordinary ChatGPT queue

## Status — 2026-11-14

The GitHub Contents API backend is implemented on this feature branch. Its isolated state-machine tests pass, but it has **not** been connected to the active Hermes process or verified against the live GitHub queue.

Render's latest successful deployment reused commit 105ad24 and still starts /var/data/msw-hermes/deployments/merge09-20261112/entrypoint.py. The production worker therefore does not run this branch's adapter. The standby worker remains an isolated probe and does not consume this queue. A secret being present in Render does not by itself load this code.

## Chosen Chat surface

The owner selected the existing hourly automation in the ordinary Chat thread. It wakes on schedule, checks the private queue, claims at most one task, performs the work in that same thread, records the actual reply, and waits for Hermes to verify and ACK before another task is assigned.

This is scheduled polling, not an immediate Hermes-triggered message. OpenAI's documented event-triggered app tasks run in Work; the documented external Workspace Agent trigger is not an interface to a personal Chat thread. Do not describe the hourly path as direct or real-time delivery.

## GitHub queue backend

- Queue file: knowledge/hermes-chat-loop/github-queue.json in private repo flyxpressWarsaw/signal-harbor-companion, branch main.
- Python module: msw_engine/github_chat_queue.py.
- Enable only with MSW_HERMES_CHAT_LOOP_ENABLED=1 and MSW_HERMES_CHAT_BACKEND=github.
- Credential: Render secret MSW_HERMES_GITHUB_TOKEN; never commit, print, or send it in ChatGPT messages.
- The adapter fixes the repository and branch in code and permits writes only to the queue file, generated work-key shards, and generated receipt paths.
- GitHub blob SHA is used for compare-and-swap. A competing claim cannot receive the same task lease.
- A stable work key is indexed by a SHA-256 shard. A retry returns the original task ID; conflicting reuse fails closed.
- The live queue holds at most one task. After result verification, Hermes ACKs it; the result and receipt are archived under knowledge/hermes-chat-loop/receipts/, and the active queue is cleared. Git history preserves prior queue revisions.
- Claimed work is never automatically reissued. Unknown outcomes remain blocked for inspection.

Queue operations are submit, claim, report, ack, and status. The task text is untrusted data and does not grant provider access, spend authority, or permission to run arbitrary code. The backend performs no model inference and sends no message into ChatGPT.

## Verification

The Python unit tests exercise submit → exclusive claim → report → digest verification → ACK, archive readback, idempotent retry, conversation binding, and restricted repository paths. They are offline tests: they do not prove that the Render secret is usable, that Hermes calls this adapter, that the ChatGPT scheduled task can write without approval, or that a live response/ACK has occurred.

## Production release gates

1. Wire the adapter into the actual Hermes process started by the production entrypoint. The active entrypoint is on Render's persistent disk and is not the normal msw_engine startup command tracked by this feature branch.
2. Keep the GitHub credential server-side and confirm its presence without printing its value.
3. Use an isolated staging worker to perform one real, uniquely identified task in the ordinary Chat thread.
4. Verify the actual ChatGPT reply, result SHA-256, Hermes readback, and explicit ACK. Then run a second distinct task to prove the loop continues without duplicate execution.
5. Only after those checks, promote the tested build and verify the production runtime.

Do not turn on the feature flag or route production traffic to this adapter until the active entrypoint and staging round-trip are verified. A passing offline test or a successful redeploy of commit 105ad24 is not an end-to-end result.

## Platform references

- https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt
- https://developers.openai.com/workspace-agents/trigger-runs
- https://docs.github.com/en/rest/repos/contents
