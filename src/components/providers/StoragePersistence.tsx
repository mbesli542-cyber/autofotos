"use client";

import { useEffect } from "react";
import { getBackendMode } from "@/lib/app-services";
import { requestPersistentStorage } from "@/lib/offline/persistent-storage";

/**
 * Demo mode keeps every vehicle and photo on this device only: asks the
 * browser once to keep that data instead of evicting it under storage
 * pressure (no effect where the Storage API is missing).
 */
export function StoragePersistence() {
  useEffect(() => {
    if (getBackendMode() === "demo") void requestPersistentStorage();
  }, []);
  return null;
}
