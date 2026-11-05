/**
 * What the camera and the vehicle page say about captures that could not be
 * saved (pure). A full device storage is told apart from a weak connection:
 * retrying alone never helps there, deleting vehicles does – and a capture
 * the queue could not even store on the device lives in memory only, so it
 * is lost when the app is closed.
 */
import { USER_ERROR_MESSAGES } from "@/lib/errors";
import type { UploadItem } from "./upload-queue";

export interface FailedUploadSummary {
  /** Failed captures a retry can still save. */
  retryable: number;
  /** Files the server refused for good – only "Verwerfen" helps. */
  refused: number;
  /** Of `retryable`: refused because the device's storage is full. */
  storageFull: number;
  /** Of `retryable`: kept in memory only – lost when the app is closed. */
  unpersisted: number;
}

export function summarizeFailedUploads(
  items: readonly Pick<UploadItem, "status" | "retryable" | "storageFull" | "unpersisted">[],
): FailedUploadSummary {
  const summary: FailedUploadSummary = { retryable: 0, refused: 0, storageFull: 0, unpersisted: 0 };
  for (const item of items) {
    if (item.status !== "failed") continue;
    if (!item.retryable) {
      summary.refused += 1;
      continue;
    }
    summary.retryable += 1;
    if (item.storageFull) summary.storageFull += 1;
    if (item.unpersisted) summary.unpersisted += 1;
  }
  return summary;
}

export const UNSAVED_PHOTOS_LOST_HINT = "Nicht gespeicherte Fotos gehen verloren, wenn die App geschlossen wird.";

/** Camera (message when the full storage refused another capture). */
export function storageFullMessage(summary: Pick<FailedUploadSummary, "unpersisted">): string {
  return summary.unpersisted > 0
    ? `${USER_ERROR_MESSAGES.storage_full} ${UNSAVED_PHOTOS_LOST_HINT}`
    : USER_ERROR_MESSAGES.storage_full;
}

/** Vehicle page: the captures a retry can still save (null: none). */
export function failedUploadsText(summary: FailedUploadSummary): string | null {
  const { retryable, storageFull, unpersisted } = summary;
  if (retryable === 0) return null;
  const photos = retryable === 1 ? "1 Foto konnte" : `${retryable} Fotos konnten`;
  const reason =
    storageFull > 0 ? `nicht gespeichert werden: ${USER_ERROR_MESSAGES.storage_full}` : "noch nicht gespeichert werden.";
  const kept = unpersisted > 0 ? UNSAVED_PHOTOS_LOST_HINT : "Sie bleiben auf diesem Gerät.";
  return `${photos} ${reason} ${kept}`;
}
