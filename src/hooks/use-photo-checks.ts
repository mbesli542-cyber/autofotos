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

/**
 * Keeps the vehicle's photos checked while a page that shows their check
 * (camera, "Fotos überprüfen", vehicle page) is visible: when the page is
 * shown with the checks on – or the checks come on while it is shown (also
 * from "not asked yet", e.g. right after a reload) – its template photos
 * without a result, check or stored rejection are checked; while the checks
 * are off because the processor is not reachable, its status is asked again
 * every minute (photoChecks.watch).
 */
export function useVehicleChecks(vehicleId: string, availability: PhotoChecksSnapshot["availability"]): void {
  useEffect(() => photoChecks.watch(), []);
  const on = availability === "on";
  useEffect(() => {
    if (on) void photoChecks.checkVehicle(vehicleId);
  }, [vehicleId, on]);
}

/** Check state per shot key of the vehicle's slots; keeps the vehicle's photos checked (useVehicleChecks). */
export function useShotCheckStates(
  vehicleId: string,
  slots: readonly PhotoSlot[],
): { states: ReadonlyMap<string, ShotCheckState>; snapshot: PhotoChecksSnapshot } {
  const snapshot = usePhotoChecks();
  useVehicleChecks(vehicleId, snapshot.availability);
  const states = useMemo(() => getShotCheckStates(slots, vehicleId, snapshot), [slots, vehicleId, snapshot]);
  return { states, snapshot };
}
