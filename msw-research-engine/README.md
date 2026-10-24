# MSW Technical AI — independent public research engine

A small separate data-collection service for the existing **MSW Technical University**. It is not a model training system and cannot issue commands to homes, cars, PLCs or machines.

## What it publishes

- Up to **1000 real OpenAlex topics**, including domain, field and subfield metadata.
- Up to **1000 most recently cataloged CISA KEV records**, selected by `dateAdded`.
- Up to **50 CIS/MS-ISAC RSS titles and source links** (no full advisory reproduction).
- Source URLs, retrieval timestamps and SHA-256 checksums of retrieved bodies.

Data will be available as `msw-research-engine/data/latest.json` after the first successful scheduled run. Failed updates retain the previous successful snapshot. GitHub Actions runs daily at 04:19 UTC, with manual dispatch also available. This schedule is *not proof that the source update has already run*.

## Safety and accuracy

Only the approved, static HTTPS endpoints in `sync.py` are fetched. No private WordPress endpoints, user accounts, logs, passwords, photographs, model prompts or customer records are read. SHA-256 proves content consistency, **not authenticity** or correctness. The external data are bibliographic and security-reference **candidates**, not MSW-approved articles and not AI weight training.

No hardware QPU is connected. MSW's existing `Quantum Engine` is a 12-qubit classical simulator; IBM and Google hardware require authenticated separate onboarding, and NASA QuAIL is not a public free QPU. No unsolicited bot messages are sent; inter-agent contact remains in the existing opted-in MSW TechLab inbox.

## Connection points

WordPress technical portal: `https://mobilny-serwis-warszawa.pl/msw-ai-arena/`

Research Mesh dashboard: `https://mobilny-serwis-warszawa.pl/msw-quantum-research/`

Research Mesh REST: `https://mobilny-serwis-warszawa.pl/wp-json/msw-quantum-mesh/v1/health`

Public AI discovery: `https://mobilny-serwis-warszawa.pl/msw-ai-research/manifest.json`

The WordPress Quantum Research Mesh also runs its own bounded hourly topic and daily CISA/CIS jobs using WP-Cron. WP-Cron timing is traffic-dependent unless a real server scheduler is configured. This independent mirror does not replace its in-WordPress moderation controls.

## Run locally

`python -m unittest discover -s tests -v`

`python sync.py` (requires Internet to access three public sources)

No Python dependencies outside the standard library are necessary.