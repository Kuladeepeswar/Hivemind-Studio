# Hivemind Studio

Type one sentence, watch a live team of AI agents (Architect → Builder → Reviewer)
plan, build, and ship a working micro-app in front of you, then click the link and
use the thing they just built.

## Layout

```
frontend/   Vite + React + Tailwind  → Zerops `static` service
api/        Node 22, Fastify + ws    → Zerops `nodejs@22` service
worker/     Python 3.12, Gemini      → Zerops `python@3.12` service
zerops.yml            build/deploy/run config for the three services above
import-project.yaml   provisions all 8 services in one shot
```

Managed services: `db` (PostgreSQL 16), `cache` (Valkey — pub/sub fan-out of live
agent events), `queue` (NATS — build job queue), `vectors` (Qdrant — embeddings of
past builds), `storage` (object storage).

## Deploy

1. Zerops GUI → **Import a project** → paste `import-project.yaml`.
2. Connect this repo to the `web`, `api`, and `worker` services (GitHub integration
   or `zcli push`). `zerops.yml` drives all three.
3. Set secrets by hand — never commit them:

   | Service | Variable | Notes |
   |---|---|---|
   | `worker` | `GEMINI_API_KEY` | required — from aistudio.google.com |
   | `worker` | `GEMINI_MODEL` | optional, defaults to `gemini-3.6-flash` |
   | `worker` | `GEMINI_EMBED_MODEL` | optional, defaults to `gemini-embedding-2` |

   Everything else (`DATABASE_URL`, `REDIS_URL`, `NATS_URL`, `QDRANT_URL`) is wired
   automatically from the managed services via `zerops.yml`.
4. Set `frontend/.env.production` to your `api` service's public subdomain before the
   first `web` build — it is baked into the static bundle at build time.

The worker applies `worker/schema.sql` on startup, so there is no separate migration
step. The schema is idempotent.

## Local development

```bash
cd worker && pip install -r requirements.txt && GEMINI_API_KEY=... python test_cli.py
```

`test_cli.py` runs the full agent loop against a hardcoded prompt with no database,
queue, or cache — the fastest way to check the agents and model IDs still work.

## Notes

- Generated apps render in `<iframe sandbox="allow-scripts">` with no
  `allow-same-origin`, plus the CSP in `frontend/src/sandbox.js`. The CDN hosts
  allowed there must stay in sync with the ones pinned in the Builder system prompt
  in `worker/agent_loop.py` — if they drift, previews render blank.
- Gemini model IDs move fast and old ones start returning 404 rather than degrading.
  Both are env-overridable so you can bump them without a redeploy of code.
