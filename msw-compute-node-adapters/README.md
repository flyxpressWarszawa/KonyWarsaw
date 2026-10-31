# MSW Render compute-node adapters 0.1.0

STAGED ONLY. Not deployed, not paired, no new server or paid plan. The main branch and running application entrypoints remain unchanged.

Target: existing WordPress Compute Mesh at https://mobilny-serwis-warszawa.pl remains the queue owner. These modules are bounded stateless executors, NOT a second broker, AI model or cluster controller.

Observed services in the owner-confirmed Robot workspace (Frankfurt, free plan):
- msw-ai-com-portal: Python
- msw-ai-centrum-robotow: Docker/Python
- msw-technical-core-360: Node.js
- msw-ai-gielda-technologii: Node.js

These are application services, not proof of four dedicated physical servers or available GPUs. Current production HTTP checks were unavailable from the testing environment; do not infer an outage or successful connection. The existing free msw-ai-roboty-db has an observed expiry of 2026-11-23T13:33:44.551964Z. Its presence does not establish that applications use it.

## Contract
GET /api/msw-compute-node/v1/manifest
POST /api/msw-compute-node/v1/execute

POST uses HTTPS + a dedicated per-node Bearer secret. Execution is disabled unless MSW_COMPUTE_NODE_ENABLED=1 and MSW_COMPUTE_NODE_TOKEN has at least 32 characters. No credential was generated or committed. Never put a secret in this repository, a download or a chat.

Operations: stats.aggregate (1-1000 finite numbers, magnitude <= 1e12), text.metrics (<=16384 UTF-8 bytes), node.probe (64-character hexadecimal challenge). Request limit 32768 bytes, 6 successful calls/minute/process. No outbound requests, shell, file access, model inference or arbitrary callbacks. Limit is not a distributed/global quota. Input must be public or owner-approved; caller is responsible for removing private data.

Request: {"schema":"msw.compute.request.v1","request_id":"example-0001","operation":"stats.aggregate","arguments":{"values":[1,2,3]}}
Node expects compact JSON.stringify wire encoding and rejects noncanonical or duplicate fields; Python also accepts whitespace but rejects duplicate keys. Metrics use each runtime's Unicode whitespace semantics, NOT LLM tokenization. Floating-point results require an appropriate comparison tolerance.

Response SHA covers request bytes. It is not a result signature, durable ACK, native engine receipt or proof that WordPress consumed the result. The returned durable_receipt, native_engine_ack and central_pairing_verified flags deliberately stay false.

## Integration still required
1. Review the current native Compute Mesh code and implement/admit its adapter for this contract; do not bypass Fotonika device authentication or create a competing queue.
2. Establish per-node credentials through owner-controlled server configuration.
3. Verify persistence/backups before restarting applications. The existing Python code has a SQLite fallback; actual DATABASE_URL was not obtained.
4. Attach the Python module once to the existing FastAPI app, or add the Node handler before the existing routing. Those entrypoints have NOT been patched here.
5. Deploy one reviewed service, test denial without credentials, a challenge, a real CPU job, result validation and independent durable readback in WordPress. Only then admit other nodes and V7/V3/V5 dispatch.

Local testing: 14 Python tests including ASGI HTTP routing; 12 Node tests including real localhost HTTP. All passed. They are not Render, WordPress/MySQL, live pairing or intelligence benchmarks. Source blobs were fetched back and matched to the locally tested bytes. Full tests and audit are in the conversation artifact MSW-RENDER-ADAPTERY-SERWEROW-0.1.0.zip.

No merge, production restart, environment change, deployment, auto-keepalive or paid resource was performed. Free Render instances can sleep and do not combine RAM across services.
