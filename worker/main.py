"""Hivemind Studio worker.

Render has no background workers on the free tier, so this runs as a small web
service instead. The API enqueues a row in `jobs` and pings POST /run to wake this
process; the queue is the source of truth, the ping is only an optimisation.

That distinction matters: on the free tier this service spins down after 15 minutes
idle. A wake ping can land while the process is still booting, or be dropped
entirely. Because work is claimed from Postgres rather than handed over in the
request body, nothing is lost when that happens - the startup drain picks it up.
"""

import asyncio
import contextlib
import json
import logging
import os
import sys

import redis.asyncio as redis
from fastapi import FastAPI
from psycopg_pool import AsyncConnectionPool

from dotenv import load_dotenv

from agent_loop import AgentLoop
from llm import LLM, NoProvidersConfigured

# Must run before the constants below read os.environ. No-op on Render, where the
# platform injects real environment variables and no .env file exists.
load_dotenv()

# psycopg's async mode refuses to run on Windows' default ProactorEventLoop. Render
# runs Linux where this is a no-op, but without it the worker cannot be run locally.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
PORT = int(os.environ.get("PORT", "10000"))

# Neon's free tier allows far fewer connections than a dedicated Postgres, and the
# old code opened a new one for every status event. One small pool instead.
pool: AsyncConnectionPool | None = None
r: redis.Redis | None = None
llm: LLM | None = None

# Guards against two overlapping drains racing each other when several wake pings
# arrive at once. SKIP LOCKED would keep it correct, this keeps it tidy.
drain_lock = asyncio.Lock()

app = FastAPI()


async def apply_schema():
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    if not os.path.exists(schema_path):
        return
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = f.read()
    async with pool.connection() as conn:
        await conn.execute(schema)
    logger.info("Schema applied.")


async def claim_job():
    """Atomically take the oldest queued job.

    FOR UPDATE SKIP LOCKED is what makes this safe to run in more than one process:
    each claimer skips rows another transaction already holds instead of blocking.
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update jobs set status = 'running',
                                attempts = attempts + 1,
                                started_at = now()
                where id = (
                    select id from jobs
                    where status = 'queued'
                    order by created_at
                    for update skip locked
                    limit 1
                )
                returning id, project_id, prompt, parent_html
                """
            )
            return await cur.fetchone()


async def finish_job(job_id: int, status: str, error: str | None = None):
    async with pool.connection() as conn:
        await conn.execute(
            "update jobs set status = %s, error = %s, finished_at = now() where id = %s",
            (status, error, job_id),
        )


async def set_project_status(project_id, status: str):
    async with pool.connection() as conn:
        await conn.execute(
            "update projects set status = %s, updated_at = now() where id = %s",
            (status, project_id),
        )


async def run_job(job_id: int, project_id, prompt: str, parent_html: str | None):
    channel = "project:" + str(project_id)

    async def publish(payload: dict, firehose: bool = False):
        message = json.dumps(payload)
        await r.publish(channel, message)
        if firehose:
            await r.publish("firehose", message)

    async def publish_status(agent, status):
        await publish({"type": "agent.status", "agent": agent, "status": status}, firehose=True)
        async with pool.connection() as conn:
            await conn.execute(
                "insert into agent_events (project_id, agent, event_type, content)"
                " values (%s, %s, %s, %s)",
                (project_id, agent, "status", status),
            )

    async def publish_token(agent, delta):
        await publish({"type": "agent.token", "agent": agent, "delta": delta})

    async def publish_artifact(html):
        await publish({"type": "artifact.update", "html": html})

    try:
        await set_project_status(project_id, "running")

        loop = AgentLoop(publish_status, publish_token, publish_artifact, llm=llm)
        html, _plan = await loop.run(prompt, parent_html)

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "insert into artifacts (project_id, html) values (%s, %s) returning id",
                    (project_id, html),
                )
                artifact_id = (await cur.fetchone())[0]

        await set_project_status(project_id, "done")
        await finish_job(job_id, "done")
        await publish({
            "type": "build.completed",
            "projectId": str(project_id),
            "artifactId": str(artifact_id),
        }, firehose=True)
        logger.info("Job %s completed for project %s", job_id, project_id)

    except Exception as exc:
        logger.error("Job %s failed for project %s: %s", job_id, project_id, exc, exc_info=True)
        with contextlib.suppress(Exception):
            await set_project_status(project_id, "failed")
            await finish_job(job_id, "failed", str(exc)[:500])
            await publish({"type": "build.failed", "error": str(exc)[:300]})


async def drain():
    """Process queued jobs until there are none left.

    Runs as a fire-and-forget task, so it swallows its own errors: an unhandled
    exception here would be an invisible dead task rather than a failed request.
    """
    if drain_lock.locked():
        logger.info("Drain already running, skipping.")
        return
    async with drain_lock:
        processed = 0
        while True:
            try:
                job = await claim_job()
            except Exception as exc:
                logger.error("Could not claim a job: %s", exc)
                break
            if job is None:
                break
            job_id, project_id, prompt, parent_html = job
            logger.info("Claimed job %s for project %s: %r", job_id, project_id, prompt)
            await run_job(job_id, project_id, prompt, parent_html)
            processed += 1
        if processed:
            logger.info("Drain finished, %d job(s) processed.", processed)


@app.on_event("startup")
async def startup():
    global pool, r, llm
    # timeout keeps /health and startup fast when the database is unreachable, rather
    # than blocking for the 30s default and failing Render's health check.
    pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=4,
                               timeout=8, open=False)
    await pool.open()
    r = redis.from_url(REDIS_URL)
    try:
        llm = LLM()
    except NoProvidersConfigured as exc:
        # Do not crash: /health should be able to report this clearly rather than
        # leaving Render in a restart loop with the reason buried in logs.
        logger.error("No LLM providers configured: %s", exc)
        llm = None

    # A transient database blip at boot must not kill the process: Render would just
    # restart it into the same failure, and /health could never report the reason.
    try:
        await apply_schema()
    except Exception as exc:
        logger.error("Could not apply schema at startup: %s", exc)

    # Pick up anything enqueued while this service was asleep or restarting.
    asyncio.create_task(drain())


@app.on_event("shutdown")
async def shutdown():
    if r is not None:
        await r.aclose()
    if pool is not None:
        await pool.close()


@app.post("/run", status_code=202)
async def run():
    """Wake-up hook. Returns immediately; the drain continues in the background."""
    asyncio.create_task(drain())
    return {"accepted": True}


@app.get("/health")
async def health():
    out = {
        "ok": True,
        "providers": [p.name for p in llm.providers] if llm else [],
        "draining": drain_lock.locked(),
    }
    if not out["providers"]:
        out["ok"] = False
        out["llm"] = "no providers configured"

    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("select count(*) from jobs where status = 'queued'")
                out["queued"] = (await cur.fetchone())[0]
        out["db"] = "up"
    except Exception as exc:
        out["db"] = "down: " + str(exc)[:200]
        out["ok"] = False

    try:
        await r.ping()
        out["redis"] = "up"
    except Exception as exc:
        out["redis"] = "down: " + str(exc)[:200]
        out["ok"] = False

    return out


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
