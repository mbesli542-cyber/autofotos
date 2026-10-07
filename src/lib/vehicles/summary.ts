import type { ShotPhotoRef } from "@/lib/domain/types";
import { sortPhotosByShotOrder } from "@/lib/shots/shot-progress";
import type { ShotTemplate } from "@/lib/shots/shot-template";

/** Cover image for lists: the first photo in listing order (normally 01 Vorne links). */
export function getCoverPhoto<T extends ShotPhotoRef>(
  template: ShotTemplate,
  photos: readonly T[],
): T | null {
  return sortPhotosByShotOrder(template, photos)[0] ?? null;
}
