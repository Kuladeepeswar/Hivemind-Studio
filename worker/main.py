import os
import json
import asyncio
import logging
from datetime import datetime

import nats
import redis.asyncio as redis
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance

import psycopg

from agent_loop import AgentLoop

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")


async def init_qdrant(qdrant):
    # Ensure collection exists
    try:
        collections = await qdrant.get_collections()
        if not any(c.name == "builds" for c in collections.collections):
            await qdrant.create_collection(
                collection_name="builds",
                vectors_config=VectorParams(size=768, distance=Distance.COSINE),
            )
    except Exception as e:
        logger.error(f"Failed to init Qdrant: {e}")

async def run_db_schema():
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    if os.path.exists(schema_path):
        with open(schema_path, 'r') as f:
            schema = f.read()
            try:
                async with await psycopg.AsyncConnection.connect(DATABASE_URL) as aconn:
                    async with aconn.cursor() as acur:
                        await acur.execute(schema)
                        logger.info("Schema applied successfully.")
            except Exception as e:
                logger.error(f"Error applying schema: {e}")

async def main():
    logger.info("Starting Hivemind Studio Worker...")
    
    # Connect to NATS
    nc = await nats.connect(NATS_URL)
    logger.info(f"Connected to NATS at {NATS_URL}")

    # Connect to Redis/Valkey
    r = redis.from_url(REDIS_URL)
    logger.info(f"Connected to Redis at {REDIS_URL}")
    
    # Connect to Qdrant
    qclient = AsyncQdrantClient(url=QDRANT_URL)
    await init_qdrant(qclient)

    async def message_handler(msg):
        subject = msg.subject
        data = json.loads(msg.data.decode())
        project_id = data.get("projectId")
        prompt = data.get("prompt")
        parent_html = data.get("parentHtml")

        logger.info(f"Received job for project {project_id}: '{prompt}'")
        
        async def publish_status(agent, status):
            msg_obj = {"type": "agent.status", "agent": agent, "status": status}
            msg_str = json.dumps(msg_obj)
            await r.publish(f"project:{project_id}", msg_str)
            await r.publish("firehose", msg_str)
            # Log event in PG
            async with await psycopg.AsyncConnection.connect(DATABASE_URL) as aconn:
                async with aconn.cursor() as acur:
                    await acur.execute(
                        "INSERT INTO agent_events (project_id, agent, event_type, content) VALUES (%s, %s, %s, %s)",
                        (project_id, agent, "status", status)
                    )

        async def publish_token(agent, delta):
            msg_obj = {"type": "agent.token", "agent": agent, "delta": delta}
            await r.publish(f"project:{project_id}", json.dumps(msg_obj))

        async def publish_artifact(html):
            msg_obj = {"type": "artifact.update", "html": html}
            await r.publish(f"project:{project_id}", json.dumps(msg_obj))

        try:
            # Update status to running
            async with await psycopg.AsyncConnection.connect(DATABASE_URL) as aconn:
                async with aconn.cursor() as acur:
                    await acur.execute("UPDATE projects SET status = 'running' WHERE id = %s", (project_id,))
            
            loop = AgentLoop(publish_status, publish_token, publish_artifact)
            html, arch_plan_str = await loop.run(prompt, parent_html)
            
            # Save artifact
            async with await psycopg.AsyncConnection.connect(DATABASE_URL) as aconn:
                async with aconn.cursor() as acur:
                    await acur.execute(
                        "INSERT INTO artifacts (project_id, html) VALUES (%s, %s) RETURNING id",
                        (project_id, html)
                    )
                    artifact_id = (await acur.fetchone())[0]
                    await acur.execute("UPDATE projects SET status = 'done' WHERE id = %s", (project_id,))
            
            # Embed and save to Qdrant
            arch_plan = json.loads(arch_plan_str)
            title = arch_plan.get("title", "App")
            text_to_embed = f"{title} {prompt}"
            
            # Use Gemini embedding
            from google import genai
            gclient = genai.Client()
            emb_res = await asyncio.to_thread(
                gclient.models.embed_content,
                model='text-embedding-004',
                contents=text_to_embed,
            )
            vector = emb_res.embeddings[0].values

            await qclient.upsert(
                collection_name="builds",
                points=[
                    PointStruct(
                        id=str(project_id),
                        vector=vector,
                        payload={"projectId": str(project_id), "prompt": prompt, "title": title}
                    )
                ]
            )

            # Publish completion
            msg_obj = {"type": "build.completed", "projectId": project_id, "artifactId": str(artifact_id)}
            msg_str = json.dumps(msg_obj)
            await r.publish(f"project:{project_id}", msg_str)
            await r.publish("firehose", msg_str)
            logger.info(f"Job completed for project {project_id}")

        except Exception as e:
            logger.error(f"Error processing job for {project_id}: {e}", exc_info=True)
            async with await psycopg.AsyncConnection.connect(DATABASE_URL) as aconn:
                async with aconn.cursor() as acur:
                    await acur.execute("UPDATE projects SET status = 'failed' WHERE id = %s", (project_id,))
            msg_obj = {"type": "build.failed", "error": str(e)}
            await r.publish(f"project:{project_id}", json.dumps(msg_obj))

    await nc.subscribe("build.jobs", queue="workers", cb=message_handler)
    logger.info("Subscribed to build.jobs queue")

    try:
        # Wait indefinitely
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        await nc.close()

if __name__ == '__main__':
    # Initialize DB Schema before listening
    asyncio.run(run_db_schema())
    asyncio.run(main())
