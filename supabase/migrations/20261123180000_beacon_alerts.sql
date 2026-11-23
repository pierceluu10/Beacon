alter table public.devices add column speech_characters integer not null default 0;
alter table public.daily_spend
  add column speech_characters integer not null default 0,
  add column alerts_sent text[] not null default '{}';

-- Now returns today's totals, which the server checks against the alert thresholds.
drop function public.add_spend(uuid, numeric);
create function public.add_spend(device uuid, usd numeric) returns public.daily_spend
language sql security definer set search_path = '' as $$
  update public.devices set spent_usd = spent_usd + usd where id = device;
  insert into public.daily_spend (day, usd) values (current_date, usd)
    on conflict (day) do update set usd = public.daily_spend.usd + excluded.usd
    returning *;
$$;

create function public.add_speech(device uuid, characters integer) returns public.daily_spend
language sql security definer set search_path = '' as $$
  update public.devices set speech_characters = speech_characters + characters where id = device;
  insert into public.daily_spend (day, speech_characters) values (current_date, characters)
    on conflict (day) do update set speech_characters = public.daily_spend.speech_characters + excluded.speech_characters
    returning *;
$$;

-- True for exactly one caller per alert per day, so concurrent requests never send the same email twice.
create function public.claim_alert(alert text) returns boolean
language sql security definer set search_path = '' as $$
  update public.daily_spend set alerts_sent = alerts_sent || alert
    where day = current_date and not (alert = any(alerts_sent))
    returning true;
$$;

revoke execute on function public.add_spend(uuid, numeric) from public, anon, authenticated;
revoke execute on function public.add_speech(uuid, integer) from public, anon, authenticated;
revoke execute on function public.claim_alert(text) from public, anon, authenticated;
