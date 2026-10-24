# MSW AI ARENA Ultra HD — isolated standalone app

This directory is reserved for the standalone FastAPI game/exchange, kept separate from `msw-ai-centrum-robotow` and the existing WordPress site.

Deployment requires uploading the complete, locally tested 3.0.0 source package (Dockerfile, main.py, arena_engine.py, static/, requirements.txt, tests/). **Do not deploy this branch as-is:** the application files are not yet present in GitHub.

The available version currently stores state in SQLite. Render free web services have ephemeral storage; do not enable public registration without persistent storage or a verified PostgreSQL migration. A `msw-ai-roboty-db` PostgreSQL instance exists in the Robot workspace but is not yet connected to the app.

Security: no payment provider is integrated, do not enable payments; do not change the existing domain. Use Render Frankfurt. Deploy only after code + persistent DB + session/security tests are verified.
