-- ===========================================================================
-- AutoExperten Photo – the image processor's hints on processed photos
--
-- vehicle_photos.processing_warnings: the hints the processor reported for
-- the processed version (jsonb array of { code, message }, German messages –
-- e.g. "Das Foto zeigt das Fahrzeug von der anderen Seite …"). The app writes
-- them together with processed_storage_path / processed_preset; a new
-- processing replaces them, a new photo (add_vehicle_photo) starts without
-- them (null).
--
-- Access is unchanged: the column belongs to the existing table, so its Row
-- Level Security policies and grants apply (members of the vehicle's
-- organisation read and update it). The original-protection trigger still
-- refuses every change of original_storage_path, vehicle_id and shot_key;
-- the processed fields – this one included – may change.
--
-- Apply after 20261028000000_storage_access_index_and_late_photo_uploads.sql.
-- Safe to run twice. The app reads the column: apply this file before the
-- app version that shows the hints is deployed.
-- ===========================================================================

alter table public.vehicle_photos
  add column if not exists processing_warnings jsonb;

comment on column public.vehicle_photos.processing_warnings is
  'Hints of the image processor on the processed version: [{ "code": "...", "message": "..." }] (German), null without.';

-- An array of a few hints (the processor reports a handful per photo). CASE
-- keeps jsonb_array_length away from anything that is not an array.
alter table public.vehicle_photos
  drop constraint if exists vehicle_photos_processing_warnings_check;
alter table public.vehicle_photos
  add constraint vehicle_photos_processing_warnings_check
  check (
    processing_warnings is null
    or case
      when jsonb_typeof(processing_warnings) = 'array'
        then jsonb_array_length(processing_warnings) <= 20
          and octet_length(processing_warnings::text) <= 16384
      else false
    end
  );

-- The Data API (PostgREST) learns about the new column.
notify pgrst, 'reload schema';
