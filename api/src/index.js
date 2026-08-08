require('dotenv').config();
const fastify = require('fastify')({ logger: true });
const cors = require('@fastify/cors');
const fastifyWebsocket = require('@fastify/websocket');
const { Pool } = require('pg');
const { connect: connectNats } = require('nats');

const DATABASE_URL = process.env.DATABASE_URL || 'postgresql://postgres:postgres@localhost:5432/postgres';
const NATS_URL = process.env.NATS_URL || 'nats://localhost:4222';

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
    try {
      await pool.query('select 1');
      health.db = 'up';
    } catch (err) {
      health.db = `down: ${err.message}`;
      health.ok = false;
    }
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

async function connectNatsForever() {
  let attempt = 0;
  for (;;) {
    attempt += 1;
    try {
      const nc = await connectNats({ servers: NATS_URL, maxReconnectAttempts: -1 });
      fastify.nc = nc;
      fastify.log.info(`Connected to NATS at ${maskUrl(NATS_URL)}`);

      // connectNats resolves once; if the link later dies for good, fall back into
      // this loop instead of silently accepting prompts we can never deliver.
      nc.closed().then((err) => {
        fastify.nc = null;
        fastify.log.error(`NATS connection closed${err ? `: ${err.message}` : ''}, reconnecting.`);
        connectNatsForever();
      });
      return;
    } catch (err) {
      // "Invalid URL" here almost always means a ${...} placeholder in zerops.yml
      // did not resolve, so log the URL (masked) rather than just the error.
      fastify.log.error(
        `NATS connect attempt ${attempt} failed: ${err.message} (url=${maskUrl(NATS_URL)})`
      );
      await new Promise((r) => setTimeout(r, Math.min(30000, 3000 * attempt)));
    }
  }
}

start();
