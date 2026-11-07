# COMBO SLM — Render-side integration candidate 0.1.0

## State, not marketing

This package implements a tested orchestration kernel, per-project retrieval memory,
a diverse design allocator, completion gates and an actual local GGUF inference
adapter. **It is not a trained language model, not a production deployment, and not
an already-working end-to-end portal factory.** No model weights are included.
No test in this package claims live model inference or live payment verification.

The existing Render worker is the execution host. WordPress/COMBO is a lightweight
signed execution endpoint. Do not run the model or the orchestration loop in PHP.
Do not create an additional Render service, cron job, database or paid plan without
separate authorization. No existing production start command is changed here.

## Implemented

- `LocalSLM`: local llama.cpp GGUF execution, streaming SHA-256 verification of the
  model and executable, observed cgroup/host memory check, loopback-only listener,
  per-process API token, restricted subprocess environment, one inference slot,
  context capped at 4096 and strict structured-output validation. No shell, remote
  URL from a job, tool execution by the model, or automatic API fallback.
- `Store`: SQLite WAL + FULL synchronization, idempotent project records, durable
  stage reservations, reviewed tenant-scoped FTS retrieval. This is retrieval
  memory, not fine-tuning or neural-weight training.
- Design allocation: per-installation identity; business-specific layout family,
  distinct primary colors, typography, hero, cards, spacing and radii; rejection
  of near-identical specifications, including simple color-only clones. Finite
  diversity is explicit; an exhausted design space is not called unique.
- `Orchestrator.tick`: exactly one deployment-owned adapter at a time, fresh
  domain/scope authorization, quarantine respected, independent read-back before
  completion, durable receipts. Uncertain writes pause for read-only reconciliation
  and are never blindly retried after a crash or timeout.
- Acceptance gate: at least 50 distinct published, reviewed, illustrated,
  purchasable and deliverable products; at least 30 distinct published, reviewed,
  illustrated articles; DNS/TLS, mobile theme, rights, privacy, tax setup, sandbox
  checkout, download entitlement, PayPal merchant/webhook, Telegram and COMBO tests.
  A queued job, mock receipt or quantity of titles is not a finished portal.

## Native integration still required

The package uses deployment-owned `Adapter(run, readback)` functions. A missing
adapter is reported as `adapter_missing`, not success. These bindings must be
implemented against the existing, authenticated hosting and COMBO contracts:

1. `domain.verify`: verify ownership through the installed GoDaddy bridge. This
   package deliberately has **no purchase method**. A purchase uses the existing
   quote/explicit approval/idempotency workflow; never invent agreement acceptance.
2. `hosting.prepare`: use cPanel's actual document-root mapping, account quotas,
   permissions, PHP and database limits. Allocate only a new, non-colliding root.
   Do not infer account quota from filesystem free bytes or overwrite another site.
3. `wordpress.bootstrap` + `combo.enroll`: native install and signed enrollment;
   discover satellites from current COMBO, not a fixed list. Only owner-approved
   domains may enter. Revocation and quarantine remain authoritative.
4. `brand.design`, `theme.stage`, `media.prepare`: business-grounded SLM planning,
   rendering/staging, screenshots and licensed/approved images. The allocator in
   `core.py` is deterministic software, not a language model. The text SLM does not
   produce photographic pixels; use a separately approved image generator.
5. `products.build` + `articles.build`: bounded, individual jobs, fact checking,
   source citations, original assets/PDFs/apps, native shop writes, public read-back.
   Do not fill the counts with placeholder products, repetitive text or empty PDFs.
6. `shop.configure`, `paypal.verify`, `telegram.verify`: native WooCommerce/payment
   connection, correct merchant, verified webhooks and entitlement, existing private
   Telegram recipient with delivery receipt. Secrets stay in server storage.
   Enabling a plugin alone is not a verified payment integration.
7. `quality.verify`, `portal.release`, `portal.verify`: obtain independent evidence,
   enforce the acceptance gate before release and repeat visitor-side checks after
   release. Acceptance consumes trusted verifier output, never SLM assertions.

A `Grant` is constructed by an authenticated adapter from a fresh COMBO/hosting
read. It must never be deserialized from an SLM reply. A model cannot approve its
own domain, receipt, purchase, rights, tax settings or release.

## Model choice and resource boundary

A starting CPU candidate is Qwen3-1.7B GGUF from the official Qwen repository;
Phi-4-mini-instruct (3.8B) or Qwen3.5-2B can be evaluated separately. This is not a
claim that any fits the current Render plan: the selected service's RAM, CPU,
persistent disk, current load and full inference benchmark must be measured first.
The launcher's model-file-size + 1.5 GiB guard is a conservative engineering budget,
not a measured upper bound on model memory. Context/KV cache and runtime overhead
can require more. Benchmark and monitor RSS before production promotion.

Pin a reviewed model file and a compatible llama.cpp executable by SHA-256 in
server-side deployment configuration. Verify the upstream license and release
provenance before provisioning. Never download executable code supplied by a job
or run `curl | sh`. No automatic model downloads are implemented by this package.

Persist the `Store` directory under the **existing mounted disk**. The package
cannot prove a mount is persistent from its path name; verify the Render service
configuration. Do not put secrets or customer documents into this public repository.

## Use inside the existing worker

```python
from pathlib import Path
from msw_combo_slm.core import Store, Orchestrator

store = Store(Path(existing_persistent_data_dir) / "combo-slm")
# trusted_grant_provider and native_adapters are deployed code, not model output.
factory = Orchestrator(store, trusted_grant_provider, native_adapters,
                       verifier=trusted_acceptance_verifier)
result = factory.tick(project_id)  # one bounded step in the existing scheduler
```

Initialize one `LocalSLM` in the existing worker supervisor only after resource
preflight. Call `start()`, check `ready()` on subsequent normal scheduler ticks,
then `generate(...)`. A running process or `/health` response alone is not an
inference test. Require successful schema-valid generation, Polish/task evals and
measured memory/latency. Shutdown calls `stop()`. Do not create a second scheduler.

## Tests

```sh
python -m unittest discover -s tests -v
```

Python 3.11+; standard library only. SQLite must provide FTS5.
The tests use synthetic adapter receipts and fake GGUF bytes **only to test guard
behavior**. They do not start a real model or contact any production service.

## Verified upstream references (2026-11-07)

- https://huggingface.co/Qwen/Qwen3-1.7B-GGUF
- https://huggingface.co/Qwen/Qwen3.5-2B
- https://huggingface.co/microsoft/Phi-4-mini-instruct
- https://github.com/ggml-org/llama.cpp/tree/master/tools/server
- https://render.com/docs/service-types
- https://render.com/docs/disks

## Release gate

This branch is a candidate for integration. Do not claim LIVE before all of:
confirmed Render workspace/service, measured capacity, pinned model + binary,
native adapter contracts/tests, authenticated queue hook, successful inference,
staging rollback, first portal end-to-end checks and signed satellite read-backs.
The unresolved `mswpc_hub_signature` error must not be fixed by disabling HMAC.
