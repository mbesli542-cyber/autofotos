"use client";

import { useEffect, useMemo, useSyncExternalStore } from "react";
import type { PhotoSlot } from "@/lib/photos/photo-slots";
import { photoChecks, type PhotoChecksSnapshot } from "@/lib/processing/photo-checks";
import { getShotCheckStates, type ShotCheckState } from "@/lib/processing/shot-check";

/**
 * The automatic checks of new captures (src/lib/processing/photo-checks.ts).
 * Starts them on first use and asks the processor status again whenever the
 * page becomes visible (routes stay mounted, effects re-run).
 */
export function usePhotoChecks(): PhotoChecksSnapshot {
  useEffect(() => {
    photoChecks.start();
    void photoChecks.refreshAvailability();
  }, []);
  return useSyncExternalStore(photoChecks.subscribe, photoChecks.getSnapshot, photoChecks.getServerSnapshot);
}

/** Check state per shot key of the vehicle's slots. */
export function useShotCheckStates(
  vehicleId: string,
  slots: readonly PhotoSlot[],
): { states: ReadonlyMap<string, ShotCheckState>; snapshot: PhotoChecksSnapshot } {
  const snapshot = usePhotoChecks();
  const states = useMemo(() => getShotCheckStates(slots, vehicleId, snapshot), [slots, vehicleId, snapshot]);
  return { states, snapshot };
}
