require('dotenv').config();
const fastify = require('fastify')({ logger: true });
const cors = require('@fastify/cors');
const fastifyWebsocket = require('@fastify/websocket');
const { Pool } = require('pg');
const { connect: connectNats } = require('nats');
const Redis = require('ioredis');

const DATABASE_URL = process.env.DATABASE_URL || 'postgresql://postgres:postgres@localhost:5432/postgres';
const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';
const NATS_URL = process.env.NATS_URL || 'nats://localhost:4222';

const pool = new Pool({ connectionString: DATABASE_URL });
const redisSubscriber = new Redis(REDIS_URL);
let nc;

async function start() {
  await fastify.register(cors, {
    origin: '*',
  });

  await fastify.register(fastifyWebsocket);

  try {
    nc = await connectNats({ servers: NATS_URL });
    fastify.log.info(`Connected to NATS at ${NATS_URL}`);
  } catch (err) {
    fastify.log.error(`Failed to connect to NATS: ${err}`);
  }

  // Decorate fastify with services so routes can use them
  fastify.decorate('db', pool);
  fastify.decorate('nc', nc);
  fastify.decorate('redisSub', redisSubscriber);

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
