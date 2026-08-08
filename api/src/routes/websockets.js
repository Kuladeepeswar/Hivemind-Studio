module.exports = async function (fastify, opts) {
  // Use a second redis client for subscription, as node_redis/ioredis 
  // requires a dedicated connection for SUB
  const Redis = require('ioredis');
  const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';

  // WS /ws/projects/:id
  fastify.get('/projects/:id', { websocket: true }, (connection, req) => {
    const { id } = req.params;
    const channel = `project:${id}`;
    
    const sub = new Redis(REDIS_URL);
    
    sub.subscribe(channel, (err, count) => {
      if (err) {
        fastify.log.error(`Failed to subscribe: ${err.message}`);
      }
    });

    sub.on('message', (chan, message) => {
      if (chan === channel) {
        connection.socket.send(message);
      }
    });

    connection.socket.on('close', () => {
      sub.quit();
    });
  });

  // WS /ws/firehose
  fastify.get('/firehose', { websocket: true }, (connection, req) => {
    const channel = 'firehose';
    const sub = new Redis(REDIS_URL);
    
    sub.subscribe(channel, (err, count) => {
      if (err) {
        fastify.log.error(`Failed to subscribe: ${err.message}`);
      }
    });

    sub.on('message', (chan, message) => {
      if (chan === channel) {
        connection.socket.send(message);
      }
    });

    connection.socket.on('close', () => {
      sub.quit();
    });
  });
};
