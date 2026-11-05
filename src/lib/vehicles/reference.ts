/**
 * "Interne Fahrzeugnummer" must be unique (pure): the export names every
 * photo AE_{Fahrzeugnummer}_{nn}_{shot}.jpg, so two vehicles with the same
 * number would give files of the same name.
 *
 * Two numbers count as the same when they give the same file name segment
 * (src/lib/naming/file-naming.ts) – ignoring case, because the files end up
 * on Windows/macOS/phone storage that does not tell "fz-1" from "FZ-1":
 * "FZ 2041", "fz-2041" and "FZ-2041" are one number.
 */
import { sanitizeFileNameSegment } from "@/lib/naming/file-naming";

export { DUPLICATE_REFERENCE_MESSAGE } from "@/lib/errors";

/** The comparison key of a reference; null when it gives no file name segment (the export then uses the vehicle id). */
export function referenceKey(reference: string | null | undefined): string | null {
  const segment = sanitizeFileNameSegment(reference ?? "");
  return segment === "" ? null : segment.toLowerCase();
}

/**
 * The other vehicle that already has this reference, or null. `ownVehicleId`:
 * the vehicle being saved (its own reference is no conflict).
 */
export function findReferenceConflict<T extends { id: string; internalReference: string | null }>(
  reference: string | null | undefined,
  vehicles: readonly T[],
  ownVehicleId: string | null = null,
): T | null {
  const key = referenceKey(reference);
  if (key === null) return null;
  return vehicles.find((vehicle) => vehicle.id !== ownVehicleId && referenceKey(vehicle.internalReference) === key) ?? null;
}
