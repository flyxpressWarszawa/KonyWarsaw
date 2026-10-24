# MSW AI Technical Core 360 — standalone

Independent Node 20+ API, interface and catalog: 40 topic families, 1000 MSW technical specializations, 1000 agent profile definitions (none running), 1200 lessons and 1600 labs generated deterministically from 20 technical tracks.

## Running

`node server.mjs` — serves `http://localhost:10000/` and API routes.

`npm test` — local read/write security smoke tests (no external calls).

No WordPress files are changed by this app. Original MSW robot Arena and Render service are preserved. Static catalog has no user secrets.

## Endpoints

- `GET /api/status` — counts and truthful state.
- `GET /api/families`, `/api/specializations`, `/api/agents` — catalog with pagination/search.
- `GET /api/curriculum?kind=lesson|lab`, `/api/curriculum/item?id=...` — 2800 outline records, safety and evidence criteria. Full MSW 2800 lessons are a separate WordPress package.
- `GET /api/quantum/bell` — mathematical 2-qubit Bell state demonstration, **not cloud QPU**.
- `GET /api/sources` — provider metadata and access restrictions.
- `GET /api/research` — metadata about latest releases checked. Not approved learning data.
- `GET /api/audit/links` — 4 links checked per run, rolling over 16 MSW project paths.
- `POST /api/internal/refresh` requires `X-MSW-Core-Key` matching environment `MSW_CORE_REFRESH_TOKEN`. Scans 8 *approved GitHub repos* and 4 MSW website pages in each run, no untrusted URLs accepted. Bounded requests. Can be called by separate Render Cron service.
- `POST /api/peer/message` returns HTTP 503 until authenticated durable storage and moderation are connected. The app does not claim messages were delivered.

## Integrations

- IBM Quantum QPU: account + authorized instance, not connected. Qiskit open-source simulation available separately.
- Google Quantum AI QPU: approved project + account, not connected. Cirq local simulator separate.
- NASA: public science APIs, not public quantum compute.
- PennyLane: local photonic simulation options, hardware access separate.
- ESCO: public skills/competences taxonomy; MSW 1000 fields are **proprietary profiles**, not 1000 formally recognized research disciplines.

## Production risk and requirements

The current research and audit results are cached in the process memory, hence **not durable after a restart or if Render free service sleeps**. To build a production archive, link Render PostgreSQL via `DATABASE_URL` and implement database persistence, credentials + editorial moderation. No automatic publication of fetched links or weight training. Do not make server publicly accessible with an empty/guessable refresh token.

This core provides a runnable engine, monitoring and calculation endpoints; it does not spawn 1000 autonomous agents and never sends control commands to locks, ECUs, PLCs, electrical distribution or machines.
