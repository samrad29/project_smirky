-- Conversation transcript and profile change log.
-- Run after 001_initial.sql. The local script uses the service role key.

create table if not exists public.conversation_messages (
  id uuid primary key default gen_random_uuid(),
  athlete_id text not null references public.athletes (athlete_id),
  role text not null check (role in ('user', 'assistant', 'system')),
  content text not null,
  created_at timestamptz not null default now()
);

create index if not exists conversation_messages_athlete_created_idx
  on public.conversation_messages (athlete_id, created_at);

create table if not exists public.profile_changes (
  id uuid primary key default gen_random_uuid(),
  athlete_id text not null references public.athletes (athlete_id),
  field text not null,
  old_value jsonb,
  new_value jsonb,
  reason text not null,
  status text not null check (status in ('applied', 'declined')),
  created_at timestamptz not null default now()
);

create index if not exists profile_changes_athlete_created_idx
  on public.profile_changes (athlete_id, created_at desc);

alter table public.conversation_messages enable row level security;
alter table public.profile_changes enable row level security;

revoke all on table public.conversation_messages from anon, authenticated;
revoke all on table public.profile_changes from anon, authenticated;
