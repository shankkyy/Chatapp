create table if not exists public.users (
  id bigint generated always as identity primary key,
  email text not null unique,
  username text not null unique,
  hashed_password text not null,
  full_name text,
  is_active boolean not null default true,
  is_admin boolean not null default false,
  bio text,
  profile_picture_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

drop table if exists public.comments;
drop table if exists public.posts;

create table if not exists public.chat_rooms (
  id bigint generated always as identity primary key,
  name text not null unique,
  created_at timestamptz not null default now()
);

create table if not exists public.chat_messages (
  id bigint generated always as identity primary key,
  room_id bigint not null references public.chat_rooms (id) on delete cascade,
  user_id bigint not null references public.users (id) on delete cascade,
  content text not null,
  created_at timestamptz not null default now()
);

create index if not exists chat_messages_room_created_idx
  on public.chat_messages (room_id, created_at);

alter table public.users enable row level security;
alter table public.chat_rooms enable row level security;
alter table public.chat_messages enable row level security;

drop policy if exists "api access" on public.users;
drop policy if exists "api access" on public.chat_rooms;
drop policy if exists "api access" on public.chat_messages;

create policy "api access" on public.users for all using (true) with check (true);
create policy "api access" on public.chat_rooms for all using (true) with check (true);
create policy "api access" on public.chat_messages for all using (true) with check (true);

grant select, insert, update, delete on public.users, public.chat_rooms, public.chat_messages to anon, authenticated;
grant usage, select on all sequences in schema public to anon, authenticated;
