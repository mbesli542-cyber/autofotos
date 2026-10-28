-- ===========================================================================
-- AutoExperten Photo – fast storage access checks, late photo uploads
--
-- 1. can_access_vehicle_object(): the storage policies call it once per
--    object. It compared v.id::text with the path, so Postgres scanned every
--    vehicle for every object (signing the covers of the vehicle list grew
--    quadratically with the fleet). It now compares the uuid column, which
--    uses the primary key. Access is unchanged: only members of the vehicle's
--    organisation, only for paths whose first segment is the vehicle id.
-- 2. add_vehicle_photo(): a capture that is older than the active photo of
--    its shot (a queued upload retried after a retake, a second device) no
--    longer archives the newer photo – it is stored as archived instead.
--    A photo counts as taken at most 5 minutes after it was saved, so a
--    device clock running ahead blocks later retakes for 5 minutes at most.
--
-- Apply after 20261007000000_initial_schema.sql. create or replace keeps
-- signature, owner and grants. Safe to run twice. Running the initial schema
-- file again restores the old functions – then run this file again as well.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- Storage helper
-- ---------------------------------------------------------------------------

-- True if the first path segment is a vehicle the current user can access.
-- SECURITY INVOKER on purpose: the vehicles RLS policies apply.
-- Only a uuid in canonical lowercase form is cast – the only form that ever
-- equalled v.id::text – so any other first segment yields false, never an
-- error.
create or replace function public.can_access_vehicle_object(object_name text)
returns boolean
language sql
stable
security invoker
set search_path = ''
as $$
  select exists (
    select 1 from public.vehicles v
    where v.id = case
      when split_part(object_name, '/', 1) ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        then split_part(object_name, '/', 1)::uuid
    end
  );
$$;

-- ---------------------------------------------------------------------------
-- Vehicle photos
-- ---------------------------------------------------------------------------

-- Adds a photo and archives the previous one of the same shot – atomically.
-- A capture older than the active photo (taken_at) is stored as archived and
-- the newer photo stays active; equal or newer captures replace it. The
-- active photo counts as taken at most 5 minutes after it was saved
-- (created_at; tolerance for device clocks), so a photo from a device whose
-- clock runs ahead keeps later retakes archived for 5 minutes at most.
-- Calls for the same shot wait for each other (transaction advisory lock), so
-- each one decides against the photo the previous call saved; the unique
-- index still guarantees one active photo per shot.
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
  active_taken_at timestamptz;
  new_archived_at timestamptz;
begin
  perform pg_advisory_xact_lock(hashtextextended(p_vehicle_id::text || '/' || p_shot_key, 0));

  select * into result from public.vehicle_photos where id = p_id;
  if found then
    return result;
  end if;

  -- Capture time of the active photo, at most 5 minutes after it was saved.
  select least(p.taken_at, p.created_at + interval '5 minutes') into active_taken_at
  from public.vehicle_photos p
  where p.vehicle_id = p_vehicle_id
    and p.shot_key = p_shot_key
    and p.archived_at is null;

  if found and p_taken_at < active_taken_at then
    -- Late older capture: keep the newer photo active.
    new_archived_at := now();
  else
    update public.vehicle_photos
    set archived_at = now()
    where vehicle_id = p_vehicle_id
      and shot_key = p_shot_key
      and archived_at is null;
  end if;

  insert into public.vehicle_photos (
    id, vehicle_id, shot_key, shot_order, title,
    original_storage_path, thumbnail_storage_path, width, height, taken_at, archived_at
  ) values (
    p_id, p_vehicle_id, p_shot_key, p_shot_order, p_title,
    p_original_storage_path, p_thumbnail_storage_path, p_width, p_height, p_taken_at, new_archived_at
  )
  returning * into result;

  return result;
end;
$$;
