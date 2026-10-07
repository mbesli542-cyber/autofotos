-- ===========================================================================
-- AutoExperten Photo – initial schema
--
-- Tables, indexes, Row Level Security, storage buckets and storage policies.
-- Run with the Supabase CLI (`supabase db push`) or paste into the SQL editor.
--
-- Access model: every employee belongs to an organisation. Vehicles belong to
-- an organisation, so all employees of AutoExperten Schwetzingen share the
-- vehicles – and nobody else can see them. New auth users are added to the
-- default organisation automatically (public sign-up must be disabled).
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- Organisations & membership
-- ---------------------------------------------------------------------------
create table if not exists public.organizations (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  name text not null,
  created_at timestamptz not null default now()
);

create table if not exists public.organization_members (
  organization_id uuid not null references public.organizations (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  role text not null default 'member' check (role in ('member', 'admin')),
  created_at timestamptz not null default now(),
  primary key (organization_id, user_id)
);

create index if not exists organization_members_user_id_idx
  on public.organization_members (user_id);

insert into public.organizations (slug, name)
values ('autoexperten-schwetzingen', 'AutoExperten Schwetzingen')
on conflict (slug) do nothing;

-- Is the current user a member of the given organisation?
create or replace function public.is_organization_member(org_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.organization_members m
    where m.organization_id = org_id
      and m.user_id = (select auth.uid())
  );
$$;

-- Default organisation of the current user (used as column default).
create or replace function public.current_organization_id()
returns uuid
language sql
stable
security definer
set search_path = ''
as $$
  select m.organization_id
  from public.organization_members m
  where m.user_id = (select auth.uid())
  order by m.created_at
  limit 1;
$$;

-- New users join the default organisation (internal tool, no public sign-up).
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.organization_members (organization_id, user_id)
  select o.id, new.id
  from public.organizations o
  where o.slug = 'autoexperten-schwetzingen'
  on conflict do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Users that already existed before this migration.
insert into public.organization_members (organization_id, user_id)
select o.id, u.id
from public.organizations o
cross join auth.users u
where o.slug = 'autoexperten-schwetzingen'
on conflict do nothing;

-- ---------------------------------------------------------------------------
-- Shared trigger: updated_at
-- ---------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- Vehicles
-- ---------------------------------------------------------------------------
create table if not exists public.vehicles (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null default public.current_organization_id()
    references public.organizations (id) on delete restrict,
  user_id uuid default auth.uid() references auth.users (id) on delete set null,
  manufacturer text not null check (char_length(manufacturer) between 1 and 60),
  model text not null check (char_length(model) between 1 and 80),
  color text check (char_length(color) <= 40),
  license_plate text check (char_length(license_plate) <= 15),
  vin text check (vin ~ '^[A-HJ-NPR-Z0-9]{17}$'),
  mileage integer check (mileage between 0 and 2000000),
  first_registration date,
  internal_reference text check (char_length(internal_reference) <= 40),
  notes text check (char_length(notes) <= 2000),
  status text not null default 'new'
    check (status in ('new', 'capturing', 'complete', 'processed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists vehicles_org_created_idx
  on public.vehicles (organization_id, created_at desc);
create index if not exists vehicles_org_status_idx
  on public.vehicles (organization_id, status);
create index if not exists vehicles_user_id_idx
  on public.vehicles (user_id);

drop trigger if exists vehicles_set_updated_at on public.vehicles;
create trigger vehicles_set_updated_at
  before update on public.vehicles
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Vehicle photos
--
-- One row per captured file. A retake inserts a NEW row and archives the
-- previous one (archived_at). original_storage_path can never change.
-- Processed results are separate files (processed_storage_path).
-- ---------------------------------------------------------------------------
create table if not exists public.vehicle_photos (
  id uuid primary key default gen_random_uuid(),
  vehicle_id uuid not null references public.vehicles (id) on delete cascade,
  shot_key text not null check (shot_key ~ '^[a-z0-9_]{1,40}$'),
  shot_order integer not null check (shot_order > 0),
  title text not null check (char_length(title) <= 120),
  original_storage_path text not null,
  processed_storage_path text,
  processed_preset text
    check (processed_preset in ('autoexperten_standard', 'autoexperten_dark', 'original_plus')),
  thumbnail_storage_path text,
  width integer check (width > 0),
  height integer check (height > 0),
  taken_at timestamptz not null default now(),
  archived_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Exactly one active photo per shot and vehicle.
create unique index if not exists vehicle_photos_active_shot_idx
  on public.vehicle_photos (vehicle_id, shot_key)
  where archived_at is null;
create index if not exists vehicle_photos_vehicle_order_idx
  on public.vehicle_photos (vehicle_id, shot_order)
  where archived_at is null;

drop trigger if exists vehicle_photos_set_updated_at on public.vehicle_photos;
create trigger vehicle_photos_set_updated_at
  before update on public.vehicle_photos
  for each row execute function public.set_updated_at();

-- Originals are immutable.
create or replace function public.protect_vehicle_photo_original()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.original_storage_path is distinct from old.original_storage_path
     or new.vehicle_id is distinct from old.vehicle_id
     or new.shot_key is distinct from old.shot_key then
    raise exception 'original photo data is immutable – insert a new photo instead';
  end if;
  return new;
end;
$$;

drop trigger if exists vehicle_photos_protect_original on public.vehicle_photos;
create trigger vehicle_photos_protect_original
  before update on public.vehicle_photos
  for each row execute function public.protect_vehicle_photo_original();

-- Adds a photo and archives the previous one of the same shot – atomically.
-- Idempotent for retries (same p_id). SECURITY INVOKER: RLS applies.
create or replace function public.add_vehicle_photo(
  p_id uuid,
  p_vehicle_id uuid,
  p_shot_key text,
  p_shot_order integer,
  p_title text,
  p_original_storage_path text,
  p_thumbnail_storage_path text default null,
  p_width integer default null,
  p_height integer default null,
  p_taken_at timestamptz default now()
)
returns public.vehicle_photos
language plpgsql
security invoker
set search_path = ''
as $$
declare
  result public.vehicle_photos;
begin
  select * into result from public.vehicle_photos where id = p_id;
  if found then
    return result;
  end if;

  update public.vehicle_photos
  set archived_at = now()
  where vehicle_id = p_vehicle_id
    and shot_key = p_shot_key
    and archived_at is null;

  insert into public.vehicle_photos (
    id, vehicle_id, shot_key, shot_order, title,
    original_storage_path, thumbnail_storage_path, width, height, taken_at
  ) values (
    p_id, p_vehicle_id, p_shot_key, p_shot_order, p_title,
    p_original_storage_path, p_thumbnail_storage_path, p_width, p_height, p_taken_at
  )
  returning * into result;

  return result;
end;
$$;

-- ---------------------------------------------------------------------------
-- Processing jobs (for the future processing service; the MVP mock
-- processor is stateless and does not use this table yet)
-- ---------------------------------------------------------------------------
create table if not exists public.processing_jobs (
  id uuid primary key default gen_random_uuid(),
  vehicle_id uuid not null references public.vehicles (id) on delete cascade,
  photo_id uuid not null references public.vehicle_photos (id) on delete cascade,
  preset text not null
    check (preset in ('autoexperten_standard', 'autoexperten_dark', 'original_plus')),
  status text not null default 'queued'
    check (status in ('queued', 'processing', 'complete', 'failed')),
  progress real not null default 0 check (progress between 0 and 1),
  result_storage_path text,
  error text,
  requested_by uuid default auth.uid() references auth.users (id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists processing_jobs_photo_idx on public.processing_jobs (photo_id);
create index if not exists processing_jobs_status_idx
  on public.processing_jobs (status, created_at)
  where status in ('queued', 'processing');

drop trigger if exists processing_jobs_set_updated_at on public.processing_jobs;
create trigger processing_jobs_set_updated_at
  before update on public.processing_jobs
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------------
alter table public.organizations enable row level security;
alter table public.organization_members enable row level security;
alter table public.vehicles enable row level security;
alter table public.vehicle_photos enable row level security;
alter table public.processing_jobs enable row level security;

drop policy if exists "Members read their organisation" on public.organizations;
create policy "Members read their organisation"
  on public.organizations for select to authenticated
  using (public.is_organization_member(id));

drop policy if exists "Users read their memberships" on public.organization_members;
create policy "Users read their memberships"
  on public.organization_members for select to authenticated
  using (user_id = (select auth.uid()));

-- Vehicles: organisation members may read, create and update.
-- No delete policy: vehicles (and their originals) are not deleted by the app.
drop policy if exists "Members read vehicles" on public.vehicles;
create policy "Members read vehicles"
  on public.vehicles for select to authenticated
  using (public.is_organization_member(organization_id));

drop policy if exists "Members create vehicles" on public.vehicles;
create policy "Members create vehicles"
  on public.vehicles for insert to authenticated
  with check (public.is_organization_member(organization_id));

drop policy if exists "Members update vehicles" on public.vehicles;
create policy "Members update vehicles"
  on public.vehicles for update to authenticated
  using (public.is_organization_member(organization_id))
  with check (public.is_organization_member(organization_id));

-- Photos: access through the vehicle's organisation.
-- No delete policy: photos are archived, never deleted.
drop policy if exists "Members read photos" on public.vehicle_photos;
create policy "Members read photos"
  on public.vehicle_photos for select to authenticated
  using (exists (
    select 1 from public.vehicles v
    where v.id = vehicle_id and public.is_organization_member(v.organization_id)
  ));

drop policy if exists "Members add photos" on public.vehicle_photos;
create policy "Members add photos"
  on public.vehicle_photos for insert to authenticated
  with check (exists (
    select 1 from public.vehicles v
    where v.id = vehicle_id and public.is_organization_member(v.organization_id)
  ));

drop policy if exists "Members update photos" on public.vehicle_photos;
create policy "Members update photos"
  on public.vehicle_photos for update to authenticated
  using (exists (
    select 1 from public.vehicles v
    where v.id = vehicle_id and public.is_organization_member(v.organization_id)
  ))
  with check (exists (
    select 1 from public.vehicles v
    where v.id = vehicle_id and public.is_organization_member(v.organization_id)
  ));

-- Processing jobs: members may read and request; status updates are made by
-- the processing service with the service role (bypasses RLS).
drop policy if exists "Members read processing jobs" on public.processing_jobs;
create policy "Members read processing jobs"
  on public.processing_jobs for select to authenticated
  using (exists (
    select 1 from public.vehicles v
    where v.id = vehicle_id and public.is_organization_member(v.organization_id)
  ));

drop policy if exists "Members request processing jobs" on public.processing_jobs;
create policy "Members request processing jobs"
  on public.processing_jobs for insert to authenticated
  with check (exists (
    select 1 from public.vehicles v
    where v.id = vehicle_id and public.is_organization_member(v.organization_id)
  ));

-- ---------------------------------------------------------------------------
-- Storage buckets (private)
--   vehicle-originals/{vehicleId}/{shotKey}/{photoId}.jpg      – never overwritten
--   vehicle-thumbnails/{vehicleId}/{shotKey}/{photoId}.jpg
--   vehicle-processed/{vehicleId}/{preset}/{shotKey}/{photoId}.jpg
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values
  ('vehicle-originals', 'vehicle-originals', false, 52428800,
    array['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif']),
  ('vehicle-thumbnails', 'vehicle-thumbnails', false, 5242880,
    array['image/jpeg', 'image/webp']),
  ('vehicle-processed', 'vehicle-processed', false, 52428800,
    array['image/jpeg', 'image/png', 'image/webp'])
on conflict (id) do nothing;

-- True if the first path segment is a vehicle the current user can access.
-- SECURITY INVOKER on purpose: the vehicles RLS policies apply.
create or replace function public.can_access_vehicle_object(object_name text)
returns boolean
language sql
stable
security invoker
set search_path = ''
as $$
  select exists (
    select 1 from public.vehicles v
    where v.id::text = split_part(object_name, '/', 1)
  );
$$;

-- Originals: read + insert only. No update/delete policy → an original can
-- never be overwritten or removed through the app.
drop policy if exists "Originals: members read" on storage.objects;
create policy "Originals: members read"
  on storage.objects for select to authenticated
  using (bucket_id = 'vehicle-originals' and public.can_access_vehicle_object(name));

drop policy if exists "Originals: members upload" on storage.objects;
create policy "Originals: members upload"
  on storage.objects for insert to authenticated
  with check (bucket_id = 'vehicle-originals' and public.can_access_vehicle_object(name));

-- Thumbnails: read + insert.
drop policy if exists "Thumbnails: members read" on storage.objects;
create policy "Thumbnails: members read"
  on storage.objects for select to authenticated
  using (bucket_id = 'vehicle-thumbnails' and public.can_access_vehicle_object(name));

drop policy if exists "Thumbnails: members upload" on storage.objects;
create policy "Thumbnails: members upload"
  on storage.objects for insert to authenticated
  with check (bucket_id = 'vehicle-thumbnails' and public.can_access_vehicle_object(name));

-- Processed results: derivatives – may be regenerated (upsert) or removed.
drop policy if exists "Processed: members read" on storage.objects;
create policy "Processed: members read"
  on storage.objects for select to authenticated
  using (bucket_id = 'vehicle-processed' and public.can_access_vehicle_object(name));

drop policy if exists "Processed: members upload" on storage.objects;
create policy "Processed: members upload"
  on storage.objects for insert to authenticated
  with check (bucket_id = 'vehicle-processed' and public.can_access_vehicle_object(name));

drop policy if exists "Processed: members update" on storage.objects;
create policy "Processed: members update"
  on storage.objects for update to authenticated
  using (bucket_id = 'vehicle-processed' and public.can_access_vehicle_object(name))
  with check (bucket_id = 'vehicle-processed' and public.can_access_vehicle_object(name));

drop policy if exists "Processed: members delete" on storage.objects;
create policy "Processed: members delete"
  on storage.objects for delete to authenticated
  using (bucket_id = 'vehicle-processed' and public.can_access_vehicle_object(name));
