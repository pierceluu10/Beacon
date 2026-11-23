-- ElevenLabs characters spoken, per device and per day, beside the Claude spend.
alter table public.devices add column speech_characters integer not null default 0;
alter table public.daily_spend add column speech_characters integer not null default 0;

create function public.add_speech(device uuid, characters integer) returns void
language sql security definer set search_path = '' as $$
  update public.devices set speech_characters = speech_characters + characters where id = device;
  insert into public.daily_spend (day, speech_characters) values (current_date, characters)
    on conflict (day) do update set speech_characters = public.daily_spend.speech_characters + excluded.speech_characters;
$$;
revoke execute on function public.add_speech(uuid, integer) from public, anon, authenticated;
