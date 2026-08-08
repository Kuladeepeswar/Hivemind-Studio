const Redis = require('ioredis');

const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';

module.exports = async function (fastify, opts) {
  // One dedicated subscriber connection per socket: in Redis/Valkey a connection in
  // subscribe mode can't be used for anything else.
  function relay(channel, connection) {
    const sub = new Redis(REDIS_URL, {
      maxRetriesPerRequest: null,
      retryStrategy: (times) => Math.min(times * 200, 5000),
    });

    // Without this listener a Valkey blip is an uncaught 'error' event, which takes
    // down the entire API process — not just this one websocket.
    sub.on('error', (err) => {
      fastify.log.error(`Valkey subscriber error on ${channel}: ${err.message}`);
    });

    sub.subscribe(channel, (err) => {
      if (err) {
        fastify.log.error(`Failed to subscribe to ${channel}: ${err.message}`);
      }
    });

    sub.on('message', (chan, message) => {
      if (chan !== channel) return;
      if (connection.socket.readyState !== connection.socket.OPEN) return;
      connection.socket.send(message);
    });

    const teardown = () => sub.quit().catch(() => sub.disconnect());
    connection.socket.on('close', teardown);
    connection.socket.on('error', teardown);
  }

  // WS /ws/projects/:id
  fastify.get('/projects/:id', { websocket: true }, (connection, req) => {
    relay(`project:${req.params.id}`, connection);
  });

  // WS /ws/firehose
  fastify.get('/firehose', { websocket: true }, (connection, req) => {
    relay('firehose', connection);
  });
};
