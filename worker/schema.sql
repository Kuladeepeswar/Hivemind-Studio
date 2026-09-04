create extension if not exists pgcrypto;

create table if not exists projects (
  id uuid primary key default gen_random_uuid(),
  prompt text not null,
  parent_id uuid references projects(id),
  status text not null default 'queued', -- queued | running | done | failed
  creator_session text not null,
  like_count int not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists agent_events (
  id bigserial primary key,
  project_id uuid not null references projects(id) on delete cascade,
  agent text not null,       -- architect | builder | reviewer | system
  event_type text not null,  -- status | token | note | error
  content text,
  created_at timestamptz not null default now()
);

create table if not exists artifacts (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  version int not null default 1,
  html text not null,
  storage_key text,
  created_at timestamptz not null default now()
);

create table if not exists likes (
  project_id uuid references projects(id) on delete cascade,
  session_id text not null,
  primary key (project_id, session_id)
);

create index if not exists agent_events_project_created_idx on agent_events (project_id, created_at);

-- Build queue. Replaces NATS: Render has no message broker on the free tier, and a
-- job row survives a sleeping worker, a failed wake-up ping and a worker restart,
-- none of which a fire-and-forget HTTP call would.
create table if not exists jobs (
  id bigserial primary key,
  project_id uuid not null references projects(id) on delete cascade,
  prompt text not null,
  parent_html text,
  status text not null default 'queued',  -- queued | running | done | failed
  attempts int not null default 0,
  error text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz
);

create index if not exists jobs_pending_idx on jobs (status, created_at);
