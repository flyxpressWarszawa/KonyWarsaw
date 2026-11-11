# MSW: approved RAG → DualMemory weight updates (research only)

This extends the **existing** signed MSW worker and the **existing** private DualMemory checkpoint. No new queue, model service, scheduled paid job, or API key is created. All live execution remains gated by the current WordPress node rights, STOP/HOLD, resource limits, and signed ACK/readback.

## Source gate

Only the original **approved** \`MSW_DATA_DIR/knowledge.sqlite3\` database can be used. Legacy Malta, ChatGPT exports, FotoAtlas, customer data and the unverified owner-imported corpus are excluded. A training example must have been reviewed by the owner and ingested with \`approved: true\` **and** the separate provenance prefix \`msw-training-approved-public:v1:\`. This prefix records a manual rights/PII review convention; it is not independent proof of rights. Do not assign it using a model's output or an unattended editorial update. Do not mark private photos, contact details, accounts, passwords or client cases as training-approved. The selector rejects common secrets and contact details, but cannot replace human review.

A signed training request selects 1–8 training IDs and **different** 1–8 validation IDs. The selector checks stored content hashes, rights markers, size bounds, disjoint documents, duplicate content and the source snapshot SHA-256. It returns no source text to the caller and performs no training or publication.

## Two-phase usage (existing \`llm.generate\` queue)

1. Ensure the private \`MSW_OWN_MODEL_PACK_*\` source and the pinned initial checkpoint are installed on Render, and that current load and cost constraints permit a bounded CPU job. If not installed, training remains **blocked**; do not replace the pack or use ambient model weights.
2. Send the owner-authorized \`msw.own-model.job.v1\` payload with \`action: "training_data_status"\`, \`request_id\`, \`train_ids\`, \`validation_ids\` to obtain \`dataset_sha256\`. This is a read-only snapshot. The \`owner_authorized\` flag does not grant rights on its own; the signed WordPress queue still enforces the existing caller's permissions.
3. Obtain the existing research checkpoint SHA-256 using a \`status\` job. Execute \`action: "train_approved"\` with that explicit \`parent_sha256\`, the previously reported \`dataset_sha256\`, selected IDs, and at most 8 steps. Never replay under a fresh request ID after an uncertain result.
4. Require \`training_performed: true\`, \`weights_modified: true\`, \`checkpoint_readback_verified: true\`, distinct source/validation evidence, and the **separate server ACK/result readback**. Compare validation NLL before and after. A candidate is promoted only to the **research** current checkpoint if held-out byte loss has not increased; it is not promoted to the production Owned LLM, OpenAI, Qwen, or client workflows.
5. Run independent Polish technical benchmark tests before any business use. A lower loss on the small selected holdout is not proof of generalization.

\`train\` without the new suffix retains its original synthetic-corpus behavior. No existing packs are modified, no new background retraining starts automatically, and failed source approval or wrong hash is fail-closed.

## Outstanding production gates

This change deliberately ships as a reviewed source branch / PR, **not** a production deployment. Real live training still requires evidence of the exact private pack and tensor checkpoint available to the worker, operational backup and rollback, valid signed training capability, current CPU/RAM headroom, approved public training examples, a clean CI run and a verified queue ACK. The known oversized Render environment variable pack issue must be addressed without copying secrets to GitHub. Neither OpenAI nor Alibaba Qwen model weights are locally editable through these handlers; provider-specific fine-tuning eligibility and credentials would have to be established separately.
