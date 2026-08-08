const { JSONCodec } = require('nats');
const jc = JSONCodec();
const crypto = require('crypto');

module.exports = async function (fastify, opts) {
  // If the job never reaches the worker, the project would sit in "queued"
  // forever with the UI spinning. Mark it failed and surface a 503 instead.
  async function enqueue(projectId, payload) {
    try {
      if (!fastify.nc || fastify.nc.isClosed()) {
        throw new Error('not connected to NATS');
      }
      fastify.nc.publish('build.jobs', jc.encode(payload));
    } catch (err) {
      fastify.log.error(`Failed to enqueue build for ${projectId}: ${err.message}`);
      try {
        await fastify.db.query("UPDATE projects SET status = 'failed' WHERE id = $1", [projectId]);
      } catch (dbErr) {
        // Best effort — don't let a second failure mask the 503 we owe the client.
        fastify.log.error(`Also failed to mark ${projectId} failed: ${dbErr.message}`);
      }
      const e = new Error(`Build queue unavailable (${err.message}). Check the API's /health.`);
      e.statusCode = 503;
      throw e;
    }
  }

  // POST /api/projects
  fastify.post('/', async (request, reply) => {
    const { prompt } = request.body;
    const sessionId = request.headers['x-session-id'] || crypto.randomUUID();
    
    const dbRes = await fastify.db.query(
      "INSERT INTO projects (prompt, creator_session) VALUES ($1, $2) RETURNING id, status",
      [prompt, sessionId]
    );
    const project = dbRes.rows[0];

    await enqueue(project.id, { projectId: project.id, prompt });

    return { id: project.id, status: project.status, sessionId };
  });

  // GET /api/projects/:id
  fastify.get('/:id', async (request, reply) => {
    const { id } = request.params;
    
    const projRes = await fastify.db.query("SELECT * FROM projects WHERE id = $1", [id]);
    if (projRes.rows.length === 0) {
      reply.code(404);
      return { error: 'Project not found' };
    }
    const project = projRes.rows[0];

    const artRes = await fastify.db.query(
      "SELECT * FROM artifacts WHERE project_id = $1 ORDER BY created_at DESC LIMIT 1",
      [id]
    );
    const artifact = artRes.rows.length > 0 ? artRes.rows[0] : null;

    return { project, artifact };
  });

  // GET /api/projects
  fastify.get('/', async (request, reply) => {
    const { sort = 'recent', limit = 10 } = request.query;
    let query = "SELECT * FROM projects WHERE status = 'done' ";
    
    if (sort === 'popular') {
      query += "ORDER BY like_count DESC, created_at DESC LIMIT $1";
    } else {
      query += "ORDER BY created_at DESC LIMIT $1";
    }

    const res = await fastify.db.query(query, [limit]);
    return res.rows;
  });

  // POST /api/projects/:id/remix
  fastify.post('/:id/remix', async (request, reply) => {
    const { id } = request.params;
    const { prompt } = request.body;
    const sessionId = request.headers['x-session-id'] || crypto.randomUUID();

    // Get parent HTML
    const artRes = await fastify.db.query(
      "SELECT html FROM artifacts WHERE project_id = $1 ORDER BY created_at DESC LIMIT 1",
      [id]
    );
    const parentHtml = artRes.rows.length > 0 ? artRes.rows[0].html : null;

    // Create new project
    const dbRes = await fastify.db.query(
      "INSERT INTO projects (prompt, parent_id, creator_session) VALUES ($1, $2, $3) RETURNING id, status",
      [prompt, id, sessionId]
    );
    const newProject = dbRes.rows[0];

    await enqueue(newProject.id, {
      projectId: newProject.id,
      prompt,
      parentHtml,
    });

    return { id: newProject.id, status: newProject.status, sessionId };
  });

  // POST /api/projects/:id/like
  fastify.post('/:id/like', async (request, reply) => {
    const { id } = request.params;
    const sessionId = request.headers['x-session-id'];
    
    if (!sessionId) {
      reply.code(400);
      return { error: 'Missing x-session-id header' };
    }

    try {
      // Upsert into likes
      await fastify.db.query(
        "INSERT INTO likes (project_id, session_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        [id, sessionId]
      );
      
      // Update like count
      await fastify.db.query(
        "UPDATE projects SET like_count = (SELECT count(*) FROM likes WHERE project_id = $1) WHERE id = $1",
        [id]
      );
      return { success: true };
    } catch (err) {
      fastify.log.error(err);
      reply.code(500);
      return { error: 'Failed to like project' };
    }
  });

  // GET /api/projects/:id/events
  fastify.get('/:id/events', async (request, reply) => {
    const { id } = request.params;
    const { after = 0 } = request.query;

    const res = await fastify.db.query(
      "SELECT * FROM agent_events WHERE project_id = $1 AND id > $2 ORDER BY id ASC",
      [id, after]
    );
    return res.rows;
  });
};
