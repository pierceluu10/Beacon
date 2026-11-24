-- Beacon's own system prompts, kept out of the public repo. Only the server (service role) reads them: no policies.
create table public.prompts (
  name text primary key,
  text text not null,
  updated_at timestamptz not null default now()
);
alter table public.prompts enable row level security;
