# Hivemind Studio

You type one sentence. Three agents (Architect, Builder, Reviewer) plan it, write it, and
argue about it while the whole thing streams into your browser. At the end you get a
working app at its own URL and a Remix button that runs the loop again with the previous
version as context.

Built for the Zerops Challenge, August 2026.

Live: https://web-2be5.prg1.zerops.app

## How a build actually runs

1. The browser POSTs a prompt to `api`. It writes a row in Postgres, publishes a job to
   NATS, and returns immediately. It never waits on the model.
2. `worker` pulls the job off the queue. Architect returns a JSON plan. Builder streams
   one self-contained HTML document. Reviewer compares that HTML against the plan and can
   send it back to Builder once.
3. Every status change and token batch gets published to a Valkey channel called
   `project:{id}`. `api` subscribes to that channel and relays it over a WebSocket, which
   is why several people can watch the same build at the same time.
4. The final HTML goes into Postgres. The title and prompt get embedded and pushed into
   Qdrant so past builds are available as context later.

Generated apps are rendered in an `<iframe sandbox="allow-scripts">` with no
`allow-same-origin`, plus a CSP that only allows the CDNs the Builder is told to use.
That is hackathon-grade isolation, not a security guarantee, and I'd say so if you asked.

## Layout

```
frontend/   Vite + React + Tailwind, built to static files
api/        Node 22, Fastify + ws, bundled with esbuild
worker/     Python 3.12, the agent loop
zerops.yml            build and run config for web / api / worker
import-project.yaml   provisions the whole project in one paste
```

Managed services doing real work: `db` (PostgreSQL 16), `cache` (Valkey, pub/sub for live
events), `queue` (NATS, build jobs), `vectors` (Qdrant, embeddings of past builds).
`storage` is in the import file but nothing writes to it yet.

## Deploying to Zerops

Import the project first: Zerops GUI, **Import a project**, paste `import-project.yaml`.
Then connect this repo to `web`, `api` and `worker`. One `zerops.yml` drives all three.

Two things you have to set by hand, and both will silently break the app if you skip them.

**NATS_URL as a project-level variable.** It is deliberately not in `zerops.yml`.
Service-level variables win over project-level ones in Zerops, so if you put it in the
yaml it shadows the real value. Set it in the project's Environment variables panel:

```
nats://<user>:<password>@queue:4222
```

Read the actual user and password off the `queue` service's Access details page.

**GEMINI_API_KEY on the worker**, from aistudio.google.com. Never commit it.

Optional overrides, all on `worker`:

| Variable | Default |
|---|---|
| `GEMINI_MODEL` | `gemini-3.6-flash` |
| `GEMINI_FALLBACK_MODEL` | `gemini-3.5-flash-lite` |
| `GEMINI_EMBED_MODEL` | `gemini-embedding-2` |

`DATABASE_URL`, `REDIS_URL`, `QDRANT_URL` and `QDRANT_API_KEY` are wired from the managed
services in `zerops.yml` and need no attention.

Last step before your first `web` build: point `frontend/.env.production` at your own
`api` subdomain. Vite bakes it into the bundle at build time, so changing it later means
rebuilding the frontend.

The worker applies `worker/schema.sql` on startup. There's no separate migration step and
the schema is idempotent, so restarts are fine.

## Checking it works

```
curl https://<your-api-host>/health
```

```json
{"ok":true,"nats":"up","db":"up","schema":"up","redis":"up"}
```

If something is down, the response includes the masked connection string it actually
received and the last error from the client. An unresolved `${...}` placeholder shows up
here immediately, which saves a lot of time compared to reading container logs.

## Running the agent loop locally

```bash
cd worker
pip install -r requirements.txt
GEMINI_API_KEY=... python test_cli.py
```

No database, queue or cache needed. It runs the full Architect to Builder to Reviewer
loop against a hardcoded prompt and writes the result to `worker/last_build.html` so you
can open it in a browser. This is the fastest way to find out whether a model ID has been
retired out from under you.

## Known rough edges

- The Qdrant vectors get written but nothing reads them yet, so there's no "similar
  builds" feature and the Architect doesn't actually have memory in the UI sense.
- `storage` is provisioned and unused. `artifacts.storage_key` is always null.
- The live transcript isn't replayed on reload. Agent status is restored from the project
  row, but the streamed tokens are gone.
- Generated apps can't make network calls. The Builder prompt forbids it and the CSP
  blocks it, so "remix this to fetch live data" won't work. Everything is client-side
  state and mock data by design.
- No auth. Sessions are a random UUID in localStorage, which is enough to stop someone
  liking the same build twice and nothing more.

## A note on the CSP

The CDN hosts allowed in `frontend/src/sandbox.js` have to stay in sync with the ones
pinned in the Builder system prompt in `worker/agent_loop.py`. If the model starts
emitting a script tag for a host that isn't in the policy, the preview renders blank with
no obvious error. If you change one, change the other.
