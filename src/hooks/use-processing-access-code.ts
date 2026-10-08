"use client";

import { useSyncExternalStore } from "react";
import { readStoredAccessCode, subscribeAccessCode } from "@/lib/processing/access-code-storage";

/** The processing access code stored on this device (null during SSR / when none). */
export function useProcessingAccessCode(): string | null {
  return useSyncExternalStore(subscribeAccessCode, readStoredAccessCode, () => null);
}
