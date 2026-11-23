create table public.devices (
  id uuid primary key,
  ip text not null,
  spent_usd numeric(12, 6) not null default 0,
  credit_requested_at timestamptz,
  created_at timestamptz not null default now()
);
create index devices_ip_created on public.devices (ip, created_at);

create table public.daily_spend (
  day date primary key default current_date,
  usd numeric(12, 6) not null default 0
);

alter table public.devices enable row level security;
alter table public.daily_spend enable row level security;
-- No policies: only the edge function, using the service role, reads or writes these.

create function public.add_spend(device uuid, usd numeric) returns numeric
language sql security definer set search_path = '' as $$
  insert into public.daily_spend (day, usd) values (current_date, usd)
    on conflict (day) do update set usd = public.daily_spend.usd + excluded.usd;
  update public.devices set spent_usd = spent_usd + usd where id = device returning spent_usd;
$$;
revoke execute on function public.add_spend(uuid, numeric) from public, anon, authenticated;
