/**
 * Capture-time order of the photos of a shot (ISO timestamps, pure).
 *
 * The NEWER capture is the current photo of a shot, whichever upload arrives
 * first: an older capture uploaded late (a queued upload retried after a
 * retake, a second device) is stored as archived and never replaces the newer
 * photo. One rule for the database (add_vehicle_photo, supabase/migrations),
 * the demo backend (MockDataProvider.savePhoto) and what the app shows
 * (photo slots, upload queue):
 * - a SAVED photo counts as taken at most 5 minutes after it was saved
 *   (createdAt): a device whose clock runs ahead blocks later retakes for
 *   5 minutes at most;
 * - equal times count as "not older" – the new capture wins.
 */

/** A saved photo counts as taken at most this long after it was saved. */
export const SAVED_CAPTURE_TOLERANCE_MS = 5 * 60_000;

/** Capture time (ms) a saved photo counts with: min(takenAt, createdAt + 5 min). NaN if unknown. */
export function savedCaptureTime(photo: { takenAt: string; createdAt: string }): number {
  const takenAt = Date.parse(photo.takenAt);
  const savedAt = Date.parse(photo.createdAt);
  if (Number.isNaN(savedAt)) return takenAt;
  if (Number.isNaN(takenAt)) return savedAt + SAVED_CAPTURE_TOLERANCE_MS;
  return Math.min(takenAt, savedAt + SAVED_CAPTURE_TOLERANCE_MS);
}

/** True if a capture (takenAt) is older than the saved photo of its shot – it then never replaces it. */
export function isOlderThanSavedPhoto(takenAt: string, photo: { takenAt: string; createdAt: string }): boolean {
  const time = Date.parse(takenAt);
  const saved = savedCaptureTime(photo);
  if (Number.isNaN(time) || Number.isNaN(saved)) return false;
  return time < saved;
}

/**
 * Order of two captures that are not saved yet (e.g. two queued uploads of
 * the same device – one clock): true if `takenAt` is older than `than`.
 */
export function isOlderCapture(takenAt: string, than: string): boolean {
  const time = Date.parse(takenAt);
  const other = Date.parse(than);
  if (Number.isNaN(time) || Number.isNaN(other)) return false;
  return time < other;
}
