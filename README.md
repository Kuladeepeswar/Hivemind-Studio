# Hivemind Studio

You type one sentence. Three agents (Architect, Builder, Reviewer) plan it, write it, and
argue about it while the whole thing streams into your browser. At the end you get a
working app at its own URL and a Remix button that runs the loop again with the previous
version as context.

Runs entirely on free tiers: Render for hosting, Neon for Postgres, and whichever of
Groq / Cerebras / OpenRouter still has quota left today.

## How a build actually runs

1. The browser POSTs a prompt to `api`. It writes a project row, inserts a row into
   `jobs`, and returns immediately. It never waits on the model.
2. `api` pings the worker to wake it up, but does not wait for that either.
3. `worker` claims the oldest job with `SELECT ... FOR UPDATE SKIP LOCKED`. Architect
   returns a JSON plan, Builder streams one self-contained HTML document, Reviewer
   compares that HTML against the plan and can send it back to Builder once.
4. Every status change and token batch is published to a Redis channel called
   `project:{id}`. `api` subscribes and relays it over a WebSocket, which is why several
   people can watch the same build at once.
5. The final HTML goes into Postgres and the job is marked done.

Generated apps render in an `<iframe sandbox="allow-scripts">` with no
`allow-same-origin`, plus a CSP that only allows the CDNs the Builder is told to use.
That is demo-grade isolation, not a security guarantee..

### Why the queue is a table and not a broker

Render has no free message broker, so the obvious move is for the API to just call the
worker over HTTP. That breaks the moment anything goes wrong, because free web services
sleep after 15 minutes and take about a minute to wake. The wake-up ping can land while
the worker is still booting, or never arrive at all.

Putting the job in Postgres first makes all of that survivable. The ping is only an
optimisation to skip the wait; if it fails, the worker drains the queue on startup and
the build still happens. `SKIP LOCKED` means a second worker could be added later
without two of them grabbing the same job.

## Layout

```
frontend/   Vite + React + Tailwind, built to static files
api/        Node 22, Fastify + ws, bundled with esbuild
worker/     Python 3.12, FastAPI wrapper around the agent loop
render.yaml Blueprint for all four Render services
```

| Service | What it is | Why |
|---|---|---|
| `hivemind-web` | Render Static Site | Free, unlimited, never sleeps |
| `hivemind-api` | Render Web Service (Node) | REST + WebSocket relay |
| `hivemind-worker` | Render Web Service (Python) | Woken over HTTP, drains the job queue |
| `hivemind-cache` | Render Key Value | Pub/sub for live agent events |
| Postgres | **Neon**, not Render | See below |

## Deploying

**Postgres lives on Neon, not Render.** Render's free Postgres is deleted 30 days after
creation. Neon's free tier does not expire. Create a Neon project and copy the pooled
connection string.

Then in Render: New → Blueprint, point it at this repo. `render.yaml` creates the static
site, both web services and the Key Value instance, and wires `REDIS_URL`, `WORKER_URL`
and `VITE_API_URL` between them automatically.

Set these by hand, because they cannot be derived:

| Service | Variable | Notes |
|---|---|---|
| `api` and `worker` | `DATABASE_URL` | The same Neon connection string on both |
| `worker` | `GROQ_API_KEY` | console.groq.com, free |
| `worker` | `CEREBRAS_API_KEY` | cloud.cerebras.ai, free |
| `worker` | `OPENROUTER_API_KEY` | Optional third fallback |

At least one LLM key is required. They are tried in the order above and the first one
that answers wins, so hitting a daily cap on one provider does not take the app down.

The worker applies `worker/schema.sql` on startup, so there is no separate migration
step. The schema is idempotent.

## Checking it works

```
curl https://hivemind-api.onrender.com/health
```

```json
{"ok":true,"db":"up","schema":"up","queued":0,"running":0,"redis":"up","worker":"awake"}
```

`worker: "asleep"` is normal, not a fault, and is deliberately excluded from `ok`. On the
free tier it sleeps whenever nobody is building. The worker has its own `/health` that
lists which LLM providers it loaded.

## Running the agent loop locally

```bash
cd worker
pip install -r requirements.txt
GROQ_API_KEY=... python test_cli.py
GROQ_API_KEY=... python test_cli.py "a markdown notes app"
```

No database, queue or cache needed. It runs the full loop against one prompt and writes
the result to `worker/last_build.html` so you can open it in a browser. This is the
fastest way to find out whether a model ID has been retired out from under you.

## Things that will bite you

**Free model IDs rotate constantly.** The whole free Llama and Qwen tier on OpenRouter
went paid in August 2026, and DeepSeek's free variants went earlier. Model names are all
env-overridable (`GROQ_MODEL`, `CEREBRAS_MODEL`, `OPENROUTER_MODEL`) specifically so you
can fix this without touching code. When everything suddenly 404s, check this first.

**These models ignore `response_format`.** Gemini let you enforce a response schema.
Llama-class models will happily wrap JSON in prose or a markdown fence anyway, so
`extract_json` in `worker/llm.py` walks the string and pulls out the first balanced
object. That is not defensive programming for its own sake, it fires regularly.

**`@fastify/websocket` v10 passes the socket as the first argument**, not
`connection.socket`. Get that wrong and every upgrade throws, so builds run perfectly on
the server while the UI sits on IDLE forever. Nothing in the browser tells you why.

**A dead dependency shows up in the browser as a CORS error.** If the API exits while
waiting on something, there is no response at all and Chrome reports a missing
`Access-Control-Allow-Origin` header, which sends you off debugging CORS. Both services
now start listening before they connect to anything, so you get an honest error and a
readable `/health` instead.

**psycopg's async mode refuses to run on Windows' default event loop.** `main.py` sets
`WindowsSelectorEventLoopPolicy` at import. It is a no-op on Render, and without it you
cannot run the worker locally at all.

**Vite needs `base: '/'`.** Render static sites rewrite unknown paths to `index.html`, so
a relative base makes `/build/:id` request `/build/assets/index.js`, get HTML back, and
render nothing.

## Free tier limits worth knowing

- Render gives 750 instance-hours a month **shared across the whole workspace**. Two web
  services that never sleep would exhaust it. Do not point an uptime pinger at both.
- Groq allows 1,000 requests/day, Cerebras 14,400/day and 1M tokens/day, OpenRouter 50/day
  until you have spent $10 lifetime. One build costs 3-5 calls.
- Neon free is 0.5 GB and 100 compute-hours per month, and does not expire.

## Known rough edges

- The first build after a quiet spell waits for the worker to boot. The UI says so rather
  than looking hung, but it is still about a minute.
- Generated apps cannot make network calls. The Builder prompt forbids it and the CSP
  blocks it, so "remix this to fetch live data" will not work. Client-side state and mock
  data by design.
- The live transcript is not replayed on reload. Agent status is restored from the
  project row, but the streamed tokens are gone.
- Output quality is noticeably below what Gemini produced. Free Llama-class models write
  shorter, plainer apps and need the Reviewer more often.
- No auth. Sessions are a random UUID in localStorage, enough to stop someone liking the
  same build twice and nothing more.

## A note on the CSP

The CDN hosts allowed in `frontend/src/sandbox.js` must stay in sync with the ones pinned
in the Builder system prompt in `worker/agent_loop.py`. If the model emits a script tag
for a host that is not in the policy, the preview renders blank with no obvious error.
If you change one, change the other.
