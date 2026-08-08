require('dotenv').config();
const fastify = require('fastify')({ logger: true });
const cors = require('@fastify/cors');
const fastifyWebsocket = require('@fastify/websocket');
const { Pool } = require('pg');
const { connect: connectNats } = require('nats');

const DATABASE_URL = process.env.DATABASE_URL || 'postgresql://postgres:postgres@localhost:5432/postgres';
const NATS_URL = process.env.NATS_URL || 'nats://localhost:4222';

const pool = new Pool({ connectionString: DATABASE_URL });

async function start() {
  await fastify.register(cors, {
    origin: '*',
  });

  await fastify.register(fastifyWebsocket);

  // Without NATS the API happily accepts prompts and drops them on the floor —
  // builds then sit in "queued" forever with no error anywhere. Retry, and if it
  // still fails, exit so Zerops restarts us rather than serving a broken API.
  let nc;
  for (let attempt = 1; attempt <= 10; attempt++) {
    try {
      nc = await connectNats({ servers: NATS_URL, maxReconnectAttempts: -1 });
      fastify.log.info(`Connected to NATS at ${NATS_URL}`);
      break;
    } catch (err) {
      fastify.log.error(`NATS connect attempt ${attempt}/10 failed: ${err.message}`);
      await new Promise((r) => setTimeout(r, 3000));
    }
  }
  if (!nc) {
    fastify.log.error('Could not reach NATS after 10 attempts, exiting.');
    process.exit(1);
  }

  // Decorate fastify with services so routes can use them
  fastify.decorate('db', pool);
  fastify.decorate('nc', nc);

  fastify.register(require('./routes/projects'), { prefix: '/api/projects' });
  fastify.register(require('./routes/websockets'), { prefix: '/ws' });

  try {
    await fastify.listen({ port: 3000, host: '0.0.0.0' });
    fastify.log.info('Server listening on port 3000');
  } catch (err) {
    fastify.log.error(err);
    process.exit(1);
  }
}

start();
