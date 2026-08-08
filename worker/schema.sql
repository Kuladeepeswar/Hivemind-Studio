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
