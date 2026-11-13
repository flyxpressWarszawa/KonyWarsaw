# MSW Hermes ↔ Gmail — staged connector (13 November 2026)

**Status: CODE PREPARED, NOT DEPLOYED, NOT AUTHORIZED FOR LIVE GMAIL ACCESS.**

Target service: Render workspace **Robot**, `msw-central-engine-worker`
(`srv-dbltdb9srm7s73ec0v00`). This service starts from a mutable
`/var/data/msw-hermes/deployments/merge09-20261112/entrypoint.py`.
Pushing a GitHub commit **does not replace this entrypoint**, and
Render autoDeploy is **off**. Do not claim the mail bridge is live.

## Account & control

Only mailbox: `pan.konradwisniewski@gmail.com`.
The ChatGPT Gmail connector authorization does **not** grant Hermes a Gmail
access token. Obtain a **separate owner-authorized Google OAuth grant** for the
Render service. Use `gmail.readonly` for initial authorization; add
`gmail.compose` only after draft functionality is requested. No broad
`https://mail.google.com/` grant, no passwords, no forwarding, no IMAP app
passwords. Keep OAuth client secret and refresh token in Render's protected
secrets, never in GitHub, WordPress, logs or job payloads.

Protected Render environment names:
- `MSW_GMAIL_ADDRESS=pan.konradwisniewski@gmail.com`
- `MSW_GMAIL_READ_ENABLED=0` initially
- `MSW_GMAIL_DRAFT_ENABLED=0` initially
- `MSW_GMAIL_CLIENT_ID` (secret)
- `MSW_GMAIL_CLIENT_SECRET` (secret)
- `MSW_GMAIL_REFRESH_TOKEN` (secret)

The module intentionally exports no send function. Its safe minimum provides
inbox IDs, metadata-only message lookup and (when separately enabled) draft
creation. Authentication, 2FA, account recovery, WordPress email-confirmation
and password-reset subject lines are withheld from Hermes. Future code must
implement additional filtering on senders and body content before any
automated reply can be considered.

## Release gates
1. Verify target service, current deployed entrypoint and backup/rollback
   capability on persistent disk.
2. Verify OAuth consent and Gmail API account identity without printing tokens.
3. Unit-test rejection of sensitive messages, disabled defaults, invalid
   recipients and IDs; test token failure without logging response bodies.
4. Explicitly opt in read-only on staging, test against a consented test mail.
5. Enable draft-only flow; human reviews drafts in Gmail.
6. Do not enable automatic sending until a separate policy, idempotency,
   approval and audit trail are implemented and tested.

**No mailbox contents and no OAuth secrets may be exported to any public repository.**
