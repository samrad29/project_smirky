-- Initial store for athlete profiles, activities, planned sessions, and reviews.
-- Run this once in the Supabase SQL editor. The local script uses the service
-- role key, which bypasses row level security. No anon policies are created.

create table if not exists public.athletes (
  athlete_id text primary key,
  profile jsonb not null,
  updated_at timestamptz not null default now()
);

create table if not exists public.activities (
  activity_id text primary key,
  athlete_id text not null references public.athletes (athlete_id),
  started_at timestamptz,
  payload jsonb not null,
  created_at timestamptz not null default now()
);

create index if not exists activities_athlete_started_idx
  on public.activities (athlete_id, started_at desc);

create table if not exists public.planned_sessions (
  id uuid primary key default gen_random_uuid(),
  athlete_id text not null references public.athletes (athlete_id),
  activity_id text not null unique references public.activities (activity_id),
  session_date date,
  payload jsonb not null,
  created_at timestamptz not null default now()
);

create table if not exists public.reviews (
  id uuid primary key default gen_random_uuid(),
  activity_id text not null references public.activities (activity_id),
  athlete_id text not null references public.athletes (athlete_id),
  model text not null,
  review jsonb not null,
  email_body text not null,
  created_at timestamptz not null default now()
);

create index if not exists reviews_activity_created_idx
  on public.reviews (activity_id, created_at desc);

alter table public.athletes enable row level security;
alter table public.activities enable row level security;
alter table public.planned_sessions enable row level security;
alter table public.reviews enable row level security;

revoke all on table public.athletes from anon, authenticated;
revoke all on table public.activities from anon, authenticated;
revoke all on table public.planned_sessions from anon, authenticated;
revoke all on table public.reviews from anon, authenticated;
