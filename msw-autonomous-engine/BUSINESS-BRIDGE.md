# Business bridge on the existing MSW worker

`MSW_BUSINESS_BRIDGE=1` starts the business and customer lanes in the existing
worker. It does not provision services or increase the compute plan. WordPress
retains the payment, Telegram and model credentials. Existing fleet, catalogue,
FotoAtlas and native callback lanes remain independent.

The fixed gateway is `/wp-json/msw-business-agent/v1/gateway`. It reuses the
existing HMAC node verifier. Reads require the original read kind; native tool
writes, product publication and customer inbox access additionally require
`module.execute`. A retained owner grant controls the bridge. Private operator
commands use `msw_business_agent_command` on the owner's private control post.
`knowledge.seed` is restricted to the authenticated owner and the expected pack
digest, and cannot be invoked by the Render node as a new owner authorization.

Owner knowledge can be provisioned through the private environment seed or
through the authenticated central knowledge endpoint. The optional private
bootstrap PHP file is a deployment input, deliberately absent from Git. Never
commit private owner packs or API credentials. A successful ingestion reports
independent SQLite digest readback; it is source memory, not verification of all
historical claims or model weight training.

Customer AI conversations opt in with `/ai` or the existing bot's
`?start=msw_ai` link. The secret-verified existing Telegram route is retained.
Other intake commands and photos keep their native handlers. `/stop` and
`/zgloszenie` close AI mode. Replies are bound to the authenticated inbound
destination on WordPress; Render does not choose a chat ID. Contact fields are
redacted before the paid model. Customer conversations never enter shared RAG.
Private queue entries expire after 30 days and stale questions are not sent.

Reply generation persists its exact input and request identity. A sending
receipt is persisted before the native send. An uncertain provider or Telegram
outcome does not authorize a new charge or duplicate message. A definitive
model failure uses a fixed reply directing the customer to the native intake.
Only an actual Telegram response and storage readback count as delivery.

Existing WooCommerce/PayPal checkout handles customer payments. Product release
requires the existing product snapshot, tested files and their digests, protected
delivery evidence and an enabled gateway. Configuration alone is not a payment
transaction test. No payout, refund or account transfer tool is provided.

Active registry capability schemas become source memory. Execution remains in
the native owner-authorized registry and the existing leased callback queues.
Do not confuse catalogue membership or heartbeat with all business effects being
verified. Obtain satellite scope from live signed COMBO and retain the existing
fleet scheduler rather than adding a second one.

During overlapping work, read the current branch and use a compare-and-swap ref
update. Preserve parallel fleet and paid API changes. Do not repeatedly trigger
the same in-progress Render build: track its deployment ID to a terminal state.
An upload timeout is an uncertain outcome; read the installed plugin version
before retrying. A queued deployment is not proof that new code is running.

Validation: Python unittest discovery and PHP WASM checks in
`wordpress/tests/business-agent-test.php` and `business-telegram-test.php`.
Private adapters require live owner-scoped receipts beyond these local tests.
