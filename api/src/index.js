require('dotenv').config();
const fastify = require('fastify')({ logger: true });
const cors = require('@fastify/cors');
const fastifyWebsocket = require('@fastify/websocket');
const { Pool } = require('pg');

const Redis = require('ioredis');

const DATABASE_URL = process.env.DATABASE_URL || 'postgresql://postgres:postgres@localhost:5432/postgres';
const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';
// Render's blueprint supplies a bare hostname via fromService `host`, so accept both
// that and a full URL. Must be the PUBLIC url: an internal-network request does not
// wake a free service that has spun down, which is exactly when we need it to.
const WORKER_URL = (() => {
  const raw = process.env.WORKER_URL || 'http://localhost:10000';
  return /^https?:\/\//.test(raw) ? raw.replace(/\/+$/, '') : `https://${raw.replace(/\/+$/, '')}`;
})();
const PORT = Number(process.env.PORT || 3000);

// Health-check only. The per-socket subscribers live in routes/websockets.js,
// because a Redis connection in subscribe mode can't run other commands.
const redisHealth = new Redis(REDIS_URL, { lazyConnect: true, maxRetriesPerRequest: 1 });
redisHealth.on('error', (err) => fastify.log.error(`Redis health client: ${err.message}`));

const pool = new Pool({ connectionString: DATABASE_URL });

// An 'error' event with no listener is an uncaught exception in Node, so a single
// dropped idle Postgres connection would take the whole API down. Log instead.
pool.on('error', (err) => {
  fastify.log.error(`Idle Postgres client error: ${err.message}`);
});

process.on('unhandledRejection', (err) => {
  fastify.log.error(`Unhandled rejection: ${err && err.stack ? err.stack : err}`);
});

async function start() {
  await fastify.register(cors, {
    origin: '*',
  });

  await fastify.register(fastifyWebsocket);

  fastify.decorate('db', pool);
  fastify.decorate('workerUrl', WORKER_URL);

  // Hitting this from a browser tells you instantly whether a 502 is "process is
  // down" or "process is up but a dependency is broken".
  fastify.get('/health', async () => {
    const health = { ok: true, db: 'unknown' };

    try {
      await pool.query('select 1');
      health.db = 'up';
    } catch (err) {
      health.db = `down: ${err.message}`;
      health.ok = false;
    }

    // The worker applies schema.sql on startup. If it has never booted, the tables
    // are missing and every POST fails with a confusing "relation does not exist".
    if (health.db === 'up') {
      try {
        const { rows } = await pool.query(
          "select count(*) filter (where status = 'queued') as queued," +
          " count(*) filter (where status = 'running') as running from jobs"
        );
        health.schema = 'up';
        health.queued = Number(rows[0].queued);
        health.running = Number(rows[0].running);
      } catch (err) {
        health.schema = `missing (worker has not applied schema.sql): ${err.message}`;
        health.ok = false;
      }
    }

    try {
      if (redisHealth.status === 'wait') await redisHealth.connect();
      await redisHealth.ping();
      health.redis = 'up';
    } catch (err) {
      health.redis = `down: ${err.message}`;
      health.ok = false;
    }

    // Deliberately not part of `ok`: on Render's free tier the worker sleeps after
    // 15 minutes idle, so "down" here is the normal resting state, not a fault.
    // Waking it is the job of the enqueue ping.
    health.worker = await probeWorker();
    health.workerUrl = WORKER_URL;

    return health;
  });

  fastify.register(require('./routes/projects'), { prefix: '/api/projects' });
  fastify.register(require('./routes/websockets'), { prefix: '/ws' });

  try {
    await fastify.listen({ port: PORT, host: '0.0.0.0' });
    fastify.log.info(`Server listening on port ${PORT}`);
  } catch (err) {
    fastify.log.error(err);
    process.exit(1);
  }
}

async function probeWorker() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3000);
  try {
    const res = await fetch(`${WORKER_URL}/health`, { signal: controller.signal });
    return res.ok ? 'awake' : `error ${res.status}`;
  } catch (err) {
    return err.name === 'AbortError' ? 'asleep (no response in 3s)' : `unreachable: ${err.message}`;
  } finally {
    clearTimeout(timer);
  }
}

start();
