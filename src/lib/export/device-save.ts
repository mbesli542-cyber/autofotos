/**
 * Handing photo files to the device: the share sheet on phones
 * (navigator.share – iOS "Bilder sichern", Android "In Galerie speichern" or
 * any app), a download everywhere else.
 *
 * - Phones (touch screen) share when the browser can share these files
 *   (navigator.canShare({ files })); desktops always download – a ZIP for a
 *   whole vehicle, the file itself for one photo.
 * - A share needs a fresh tap (transient user activation): the files are
 *   loaded BEFORE the share button is tapped, and the share is started
 *   without waiting for anything in between.
 * - Browsers limit one share (Chrome on Android: 10 files, 50 MB): a large
 *   set is shared in parts of at most SHARE_BATCH_LIMITS, one tap each.
 * - Cancelling the share sheet is no error (AbortError → "cancelled").
 */
import { AppError, isNetworkError } from "@/lib/errors";

export type SaveMethod = "share" | "download";

/** Files and bytes per share – Chrome on Android refuses more than 10 files or 50 MB at once. */
export const SHARE_BATCH_LIMITS = { maxFiles: 10, maxBytes: 48 * 1024 * 1024 } as const;

export interface ShareBatchLimits {
  maxFiles: number;
  maxBytes: number;
}

/**
 * Splits files into shares of at most `maxFiles` files and `maxBytes` bytes,
 * keeping their order (a single file above `maxBytes` is a share of its own).
 */
export function splitIntoShareBatches<T extends { size: number }>(
  files: readonly T[],
  limits: ShareBatchLimits = SHARE_BATCH_LIMITS,
): T[][] {
  const batches: T[][] = [];
  let current: T[] = [];
  let bytes = 0;
  for (const file of files) {
    if (current.length > 0 && (current.length >= limits.maxFiles || bytes + file.size > limits.maxBytes)) {
      batches.push(current);
      current = [];
      bytes = 0;
    }
    current.push(file);
    bytes += file.size;
  }
  if (current.length > 0) batches.push(current);
  return batches;
}

/** Share on phones whose browser can share the files; download otherwise. */
export function chooseSaveMethod({ touch, canShare }: { touch: boolean; canShare: boolean }): SaveMethod {
  return touch && canShare ? "share" : "download";
}

/** A phone or tablet (coarse pointer, no hover): the share sheet is the way to the gallery. */
export function isTouchDevice(win: Pick<Window, "matchMedia"> | undefined = globalThis.window): boolean {
  if (!win || typeof win.matchMedia !== "function") return false;
  return win.matchMedia("(pointer: coarse)").matches && win.matchMedia("(hover: none)").matches;
}

type ShareNavigator = Partial<Pick<Navigator, "share" | "canShare">> & {
  userActivation?: { isActive: boolean };
};

function currentNavigator(): ShareNavigator | undefined {
  return typeof navigator === "undefined" ? undefined : (navigator as ShareNavigator);
}

/** The browser can share exactly these files (type and count). */
export function canShareFiles(files: readonly File[], nav: ShareNavigator | undefined = currentNavigator()): boolean {
  if (!nav || typeof nav.share !== "function" || typeof nav.canShare !== "function" || files.length === 0) return false;
  try {
    return nav.canShare({ files: [...files] });
  } catch {
    return false;
  }
}

/**
 * A share may still be started from the last tap: false once the browser
 * says the tap has expired (navigator.userActivation – Safari 16.4+,
 * Chrome); without that API it is assumed to be fresh.
 */
export function hasFreshTap(nav: ShareNavigator | undefined = currentNavigator()): boolean {
  return nav?.userActivation?.isActive !== false;
}

export const SHARE_FAILED_MESSAGE =
  "Das Teilen hat nicht funktioniert. Bitte die Fotos stattdessen herunterladen.";

/** The share sheet could not be shown or the files were refused (not a cancel). */
export class ShareFailedError extends AppError {
  constructor(cause: unknown) {
    super(isNetworkError(cause) ? "network" : "unknown", { userMessage: SHARE_FAILED_MESSAGE, cause });
    this.name = "ShareFailedError";
  }
}

/**
 * Opens the share sheet with these files. "cancelled" when the employee
 * closed it; any other refusal (too many/large files, the tap expired, not
 * supported) throws ShareFailedError. Call it straight from the tap – no
 * awaiting before it.
 */
export async function shareFiles(
  files: readonly File[],
  nav: ShareNavigator | undefined = currentNavigator(),
): Promise<"shared" | "cancelled"> {
  if (!nav || typeof nav.share !== "function") throw new ShareFailedError(new Error("Web Share not supported"));
  try {
    // Files only: a title or text next to them hides "Bilder sichern" on iOS.
    await nav.share({ files: [...files] });
    return "shared";
  } catch (error) {
    if (typeof error === "object" && error !== null && (error as { name?: unknown }).name === "AbortError") {
      return "cancelled";
    }
    throw new ShareFailedError(error);
  }
}

/** How long a download link stays valid (the browser reads the Blob after the click). */
const DOWNLOAD_URL_LIFETIME_MS = 60_000;

/** Downloads a Blob under this file name (a link with the download attribute). */
export function downloadBlob(blob: Blob, fileName: string, doc: Document = document): void {
  const url = URL.createObjectURL(blob);
  const link = doc.createElement("a");
  link.href = url;
  link.download = fileName;
  link.rel = "noopener";
  link.style.display = "none";
  doc.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), DOWNLOAD_URL_LIFETIME_MS);
}
