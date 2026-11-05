/**
 * Persistent storage for the data kept on this device (demo mode: vehicles,
 * photos and results in IndexedDB). Without it the browser may evict the
 * site's data under storage pressure. Browsers without the Storage API (or
 * that refuse) keep working as before – nothing here ever throws.
 */

/** The parts of the StorageManager used here (missing in older browsers). */
export interface PersistableStorage {
  persisted?: () => Promise<boolean>;
  persist?: () => Promise<boolean>;
  estimate?: () => Promise<{ usage?: number; quota?: number }>;
}

/** Whether the data is (now) kept persistently: true/false, null when the browser cannot tell. */
export async function persistStorage(storage: PersistableStorage | undefined): Promise<boolean | null> {
  if (!storage || typeof storage.persist !== "function") return null;
  try {
    if (typeof storage.persisted === "function" && (await storage.persisted())) return true;
    return await storage.persist();
  } catch {
    return null;
  }
}

function browserStorage(): PersistableStorage | undefined {
  return typeof navigator === "undefined" ? undefined : (navigator.storage as PersistableStorage | undefined);
}

let request: Promise<boolean | null> | null = null;

/** Asks the browser once per page load to keep this site's data (see persistStorage). */
export function requestPersistentStorage(): Promise<boolean | null> {
  request ??= persistStorage(browserStorage());
  return request;
}

/** Bytes this site stores on the device (null: unknown). */
export async function storageUsageBytes(storage: PersistableStorage | undefined = browserStorage()): Promise<number | null> {
  if (!storage || typeof storage.estimate !== "function") return null;
  try {
    const { usage } = await storage.estimate();
    return typeof usage === "number" && Number.isFinite(usage) ? usage : null;
  } catch {
    return null;
  }
}

/** 36_100_000 → "36 MB", 1_500_000_000 → "1,5 GB" (German, decimal units like the phone's settings). */
export function formatStorageSize(bytes: number): string {
  if (bytes >= 1e9) {
    return `${(bytes / 1e9).toLocaleString("de-DE", { maximumFractionDigits: 1 })} GB`;
  }
  return `${Math.max(1, Math.round(bytes / 1e6)).toLocaleString("de-DE")} MB`;
}
