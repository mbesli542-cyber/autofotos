/**
 * Which photos the processor gets, and what happens to them (pure).
 *
 * - exterior shots (01–08)          → AutoExperten showroom (cut-out + shadow + branding, 4:3)
 * - interior / detail shots (09–15) → sent too; the processor returns them in
 *                                     their ORIGINAL environment (never composited)
 * - extra photos (extra_*)          → not processed
 * The processor decides by the shotKey, so the correct key is always sent.
 */
import { getShot, type ShotTemplate } from "@/lib/shots/shot-template";

export type ShotTreatment = "showroom" | "original_environment";

export const SHOT_TREATMENT_LABELS: Record<ShotTreatment, string> = {
  showroom: "AutoExperten Showroom",
  original_environment: "Innenraum/Detail – Originalumgebung",
};

/** null → the photo is not processed (extra photos, unknown shots). */
export function getShotTreatment(template: ShotTemplate, shotKey: string): ShotTreatment | null {
  const shot = getShot(template, shotKey);
  if (!shot) return null;
  return shot.category === "exterior" ? "showroom" : "original_environment";
}

/** Photos sent to the processor (template shots only, input order kept). */
export function selectPhotosForProcessing<T extends { shotKey: string }>(
  template: ShotTemplate,
  photos: readonly T[],
): T[] {
  return photos.filter((photo) => getShotTreatment(template, photo.shotKey) !== null);
}

export function countByTreatment(
  template: ShotTemplate,
  photos: readonly { shotKey: string }[],
): Record<ShotTreatment, number> {
  const counts: Record<ShotTreatment, number> = { showroom: 0, original_environment: 0 };
  for (const photo of photos) {
    const treatment = getShotTreatment(template, photo.shotKey);
    if (treatment) counts[treatment] += 1;
  }
  return counts;
}
