/**
 * Demo mode: the processing access code (PROCESSING_ACCESS_CODE) entered on
 * this device. Kept in localStorage; falls back to memory when storage is
 * blocked (private mode), so processing still works for the current visit.
 */
const STORAGE_KEY = "ae-photo.processing-access-code";
const CHANGE_EVENT = "ae-photo:processing-access-code";

let memoryCode: string | null = null;

export function readStoredAccessCode(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(STORAGE_KEY) || null;
  } catch {
    return memoryCode;
  }
}

/** Stores (or with null: forgets) the code and notifies subscribers. */
export function storeAccessCode(code: string | null): void {
  const value = code?.trim() || null;
  memoryCode = value;
  try {
    if (value) window.localStorage.setItem(STORAGE_KEY, value);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // storage blocked – memory fallback above
  }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function subscribeAccessCode(listener: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (event.key === null || event.key === STORAGE_KEY) listener();
  };
  window.addEventListener(CHANGE_EVENT, listener);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(CHANGE_EVENT, listener);
    window.removeEventListener("storage", onStorage);
  };
}
