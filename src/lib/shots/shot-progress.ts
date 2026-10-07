/**
 * Pure logic for shot completion, ordering and navigation.
 * No React, no I/O – fully unit tested.
 */
import type { ShotPhotoRef } from "@/lib/domain/types";
import {
  getOrderedShots,
  getRequiredShots,
  type ShotDefinition,
  type ShotTemplate,
} from "./shot-template";

/**
 * Sorts photos in the final listing order defined by the template.
 * Photos for unknown/extra shots follow after all template shots.
 * NEVER sorts by capture or upload time.
 */
export function sortPhotosByShotOrder<T extends ShotPhotoRef>(
  template: ShotTemplate,
  photos: readonly T[],
): T[] {
  const templateOrder = new Map(
    template.shots.map((shot) => [shot.key, shot.order]),
  );
  const UNKNOWN_SHOT_OFFSET = 1_000_000;
  const rank = (photo: T): number =>
    templateOrder.get(photo.shotKey) ?? UNKNOWN_SHOT_OFFSET + photo.shotOrder;

  return [...photos].sort((a, b) => {
    const diff = rank(a) - rank(b);
    if (diff !== 0) return diff;
    return a.shotKey.localeCompare(b.shotKey);
  });
}

/** Index photos by shot key (one active photo per shot). */
export function indexPhotosByShotKey<T extends ShotPhotoRef>(
  photos: readonly T[],
): Map<string, T> {
  return new Map(photos.map((photo) => [photo.shotKey, photo]));
}

export function getCapturedShotKeys(
  photos: readonly ShotPhotoRef[],
): Set<string> {
  return new Set(photos.map((photo) => photo.shotKey));
}

export function getMissingRequiredShots(
  template: ShotTemplate,
  capturedKeys: ReadonlySet<string>,
): ShotDefinition[] {
  return getRequiredShots(template).filter(
    (shot) => !capturedKeys.has(shot.key),
  );
}

export function countCapturedRequired(
  template: ShotTemplate,
  capturedKeys: ReadonlySet<string>,
): number {
  return getRequiredShots(template).filter((shot) => capturedKeys.has(shot.key))
    .length;
}

export function isShotSetComplete(
  template: ShotTemplate,
  capturedKeys: ReadonlySet<string>,
): boolean {
  return getMissingRequiredShots(template, capturedKeys).length === 0;
}

export interface ShotProgressSummary {
  captured: number;
  required: number;
  remaining: number;
  isComplete: boolean;
  /** 0..1 */
  ratio: number;
}

export function getShotProgress(
  template: ShotTemplate,
  capturedKeys: ReadonlySet<string>,
): ShotProgressSummary {
  const required = getRequiredShots(template).length;
  const captured = countCapturedRequired(template, capturedKeys);
  return {
    captured,
    required,
    remaining: required - captured,
    isComplete: captured === required,
    ratio: required === 0 ? 1 : captured / required,
  };
}

/** First required shot without a photo, in template order. */
export function getFirstMissingShotKey(
  template: ShotTemplate,
  capturedKeys: ReadonlySet<string>,
): string | null {
  return getMissingRequiredShots(template, capturedKeys)[0]?.key ?? null;
}

/**
 * Where the guided camera goes after a successful capture: the next shot
 * without a photo AFTER the current one (wrapping around to earlier gaps).
 * Returns null when every required shot has a photo.
 */
export function getNextShotKeyAfterCapture(
  template: ShotTemplate,
  capturedKeys: ReadonlySet<string>,
  currentKey: string,
): string | null {
  const shots = getOrderedShots(template).filter((shot) => shot.required);
  const currentIndex = shots.findIndex((shot) => shot.key === currentKey);
  for (let step = 1; step <= shots.length; step += 1) {
    const candidate = shots[(currentIndex + step + shots.length) % shots.length];
    if (candidate && !capturedKeys.has(candidate.key)) return candidate.key;
  }
  return null;
}

/** Previous/next shot in template order (no wrapping). */
export function getAdjacentShotKey(
  template: ShotTemplate,
  currentKey: string,
  direction: 1 | -1,
): string | null {
  const shots = getOrderedShots(template);
  const index = shots.findIndex((shot) => shot.key === currentKey);
  if (index === -1) return null;
  return shots[index + direction]?.key ?? null;
}

/** 0-based position of a shot in template order (-1 if unknown). */
export function getShotIndex(template: ShotTemplate, key: string): number {
  return getOrderedShots(template).findIndex((shot) => shot.key === key);
}
