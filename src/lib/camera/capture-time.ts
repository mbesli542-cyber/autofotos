/**
 * Capture times (ISO) for new photos (pure).
 *
 * A photo keeps its real capture time ("Aufgenommen am"); which photo of a
 * shot is the current one follows src/lib/photos/capture-order.ts (the same
 * rule as the database). A capture with the camera or a retake is taken
 * now; a file picked as a new additional photo keeps its own date.
 */

/** Capture time of a picked file: its date, but never later than now. */
export function pickedFileTakenAt(file: { lastModified: number }, now: number = Date.now()): string {
  const time = file.lastModified > 0 && file.lastModified < now ? file.lastModified : now;
  return new Date(time).toISOString();
}
