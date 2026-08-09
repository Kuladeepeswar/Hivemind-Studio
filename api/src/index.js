require('dotenv').config();
const fastify = require('fastify')({ logger: true });
const cors = require('@fastify/cors');
const fastifyWebsocket = require('@fastify/websocket');
const { Pool } = require('pg');
const { connect: connectNats } = require('nats');

const Redis = require('ioredis');

const DATABASE_URL = process.env.DATABASE_URL || 'postgresql://postgres:postgres@localhost:5432/postgres';
const NATS_URL = process.env.NATS_URL || 'nats://localhost:4222';
const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';

// Surfaced on /health so a NATS failure is diagnosable without reading container logs.
let lastNatsError = null;

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

  // Decorate fastify with services so routes can use them. `nc` starts null and is
  // filled in by connectNatsForever below; routes must check it before publishing.
  fastify.decorate('db', pool);
  fastify.decorate('nc', null);

  // Cheap liveness probe: hitting this from a browser tells you instantly whether
  // a 502 is "process is down" or "process is up but a dependency is broken".
  fastify.get('/health', async () => {
    const health = {
      ok: true,
      nats: fastify.nc && !fastify.nc.isClosed() ? 'up' : 'down',
      natsUrl: maskUrl(NATS_URL),
      db: 'unknown',
    };
    if (health.nats !== 'up' && lastNatsError) health.natsError = lastNatsError;
    try {
      await pool.query('select 1');
      health.db = 'up';
    } catch (err) {
      health.db = `down: ${err.message}`;
      health.ok = false;
    }

    // The worker applies schema.sql on startup. If it never booted, the tables are
    // missing and every POST fails with a confusing "relation does not exist".
    if (health.db === 'up') {
      try {
        await pool.query('select 1 from projects limit 1');
        health.schema = 'up';
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
    health.redisUrl = maskUrl(REDIS_URL);

    if (health.nats !== 'up') health.ok = false;
    return health;
  });

  fastify.register(require('./routes/projects'), { prefix: '/api/projects' });
  fastify.register(require('./routes/websockets'), { prefix: '/ws' });

  // Listen BEFORE connecting to NATS. If the API dies waiting on a dependency, the
  // browser sees a bare connection failure and reports it as a CORS error, which
  // hides the real cause. Staying up means /health is readable and POST /api/projects
  // returns an honest 503.
  try {
    await fastify.listen({ port: 3000, host: '0.0.0.0' });
    fastify.log.info('Server listening on port 3000');
  } catch (err) {
    fastify.log.error(err);
    process.exit(1);
  }

  connectNatsForever();
}

// Masks the password so a connection string is safe to log or expose on /health.
function maskUrl(url) {
  return String(url).replace(/\/\/([^:/@]*):([^@]*)@/, '//$1:***@');
}

/**
 * Split a nats:// URL into connect() options.
 *
 * nats.js builds its authenticator from opts.user/opts.pass ONLY — it does not
 * read credentials out of the URL (see buildAuthenticator in
 * nats/lib/nats-base-client/options.js). Passing the full URL therefore connects
 * anonymously and the server rejects it with an authorization violation. Zerops
 * hands you credentials embedded in NATS_URL, so they have to be pulled out here.
 */
function natsConnectOptions(url) {
  try {
    const u = new URL(url);
    const opts = {
      servers: `${u.hostname}:${u.port || 4222}`,
      maxReconnectAttempts: -1,
    };
    if (u.username) {
      opts.user = decodeURIComponent(u.username);
      opts.pass = decodeURIComponent(u.password || '');
    }
    return opts;
  } catch (err) {
    // Not parseable (usually an unresolved ${...} placeholder) — hand it to
    // nats.js as-is so the error message points at the real problem.
    return { servers: url, maxReconnectAttempts: -1 };
  }
}

async function connectNatsForever() {
  let attempt = 0;
  for (;;) {
    attempt += 1;
    try {
      const nc = await connectNats(natsConnectOptions(NATS_URL));
      fastify.nc = nc;
      fastify.log.info(`Connected to NATS at ${maskUrl(NATS_URL)}`);

      // connectNats resolves once; if the link later dies for good, fall back into
      // this loop instead of silently accepting prompts we can never deliver.
      nc.closed().then((err) => {
        fastify.nc = null;
        lastNatsError = err ? err.message : 'connection closed';
        fastify.log.error(`NATS connection closed${err ? `: ${err.message}` : ''}, reconnecting.`);
        connectNatsForever();
      });
      return;
    } catch (err) {
      // "Invalid URL" here almost always means a ${...} placeholder in zerops.yml
      // did not resolve, so log the URL (masked) rather than just the error.
      lastNatsError = err.message;
      fastify.log.error(
        `NATS connect attempt ${attempt} failed: ${err.message} (url=${maskUrl(NATS_URL)})`
      );
      await new Promise((r) => setTimeout(r, Math.min(30000, 3000 * attempt)));
    }
  }
}

start();
