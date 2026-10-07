/**
 * View model for photo grids: one slot per template shot (in template
 * order), plus additional photos. Combines saved photos with local uploads
 * that are still pending.
 */
import type { VehiclePhotoWithUrls } from "@/lib/domain/types";
import type { UploadItem } from "@/lib/offline/upload-queue";
import { sortPhotosByShotOrder } from "@/lib/shots/shot-progress";
import {
  getOrderedShots,
  type ShotCategory,
  type ShotTemplate,
} from "@/lib/shots/shot-template";

export interface PhotoSlot {
  key: string;
  order: number;
  title: string;
  required: boolean;
  category: ShotCategory | null;
  photo: VehiclePhotoWithUrls | null;
  /** Newest local capture for this shot that is not uploaded yet. */
  pending: UploadItem | null;
}

export function isSlotCaptured(slot: PhotoSlot): boolean {
  return slot.photo !== null || slot.pending !== null;
}

export function buildPhotoSlots(
  template: ShotTemplate,
  photos: readonly VehiclePhotoWithUrls[],
  pendingItems: readonly UploadItem[] = [],
): { required: PhotoSlot[]; extras: PhotoSlot[] } {
  const photoByKey = new Map(photos.map((photo) => [photo.shotKey, photo]));
  const pendingByKey = new Map<string, UploadItem>();
  for (const item of pendingItems) pendingByKey.set(item.shotKey, item);

  const templateKeys = new Set(template.shots.map((shot) => shot.key));
  const required = getOrderedShots(template).map<PhotoSlot>((shot) => ({
    key: shot.key,
    order: shot.order,
    title: shot.title,
    required: shot.required,
    category: shot.category,
    photo: photoByKey.get(shot.key) ?? null,
    pending: pendingByKey.get(shot.key) ?? null,
  }));

  const extraRefs = new Map<string, { shotKey: string; shotOrder: number; title: string }>();
  for (const photo of photos) {
    if (!templateKeys.has(photo.shotKey)) extraRefs.set(photo.shotKey, photo);
  }
  for (const item of pendingItems) {
    if (!templateKeys.has(item.shotKey) && !extraRefs.has(item.shotKey)) {
      extraRefs.set(item.shotKey, { shotKey: item.shotKey, shotOrder: item.shotOrder, title: item.title });
    }
  }
  const extras = sortPhotosByShotOrder(template, [...extraRefs.values()]).map<PhotoSlot>((ref) => ({
    key: ref.shotKey,
    order: ref.shotOrder,
    title: ref.title,
    required: false,
    category: null,
    photo: photoByKey.get(ref.shotKey) ?? null,
    pending: pendingByKey.get(ref.shotKey) ?? null,
  }));

  return { required, extras };
}

/** "01".."15"; extra photos are shown as "+1", "+2", … */
export function formatSlotNumber(slot: Pick<PhotoSlot, "order" | "required">): string {
  if (!slot.required && slot.order > 100) return `+${slot.order - 100}`;
  return String(slot.order).padStart(2, "0");
}
