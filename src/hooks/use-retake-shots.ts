"use client";

import { useEffect, useMemo, useSyncExternalStore } from "react";
import { getShotTemplate } from "@/lib/app-services";
import type { VehicleSummary } from "@/lib/domain/types";
import { photoChecks } from "@/lib/processing/photo-checks";
import { vehicleRetakeShotKeys } from "@/lib/processing/retake-summary";

const NO_KEYS: readonly string[] = [];

/**
 * Keys of the shots to retake per vehicle id, template order (vehicle lists:
 * "Neu aufnehmen" badge, Kamera tab) – from the automatic checks of this
 * device (rejections are kept across reloads) and the hints on the saved
 * results. Starts the checks' store only; it never asks the processor.
 */
export function useVehicleRetakeShots(summaries: readonly VehicleSummary[]): {
  get(vehicleId: string): readonly string[];
} {
  useEffect(() => {
    photoChecks.start();
  }, []);
  const snapshot = useSyncExternalStore(photoChecks.subscribe, photoChecks.getSnapshot, photoChecks.getServerSnapshot);
  return useMemo(() => {
    const template = getShotTemplate();
    const byVehicle = new Map(
      summaries.map((summary) => [
        summary.vehicle.id,
        vehicleRetakeShotKeys(template, summary.vehicle.id, summary.photos, snapshot),
      ]),
    );
    return { get: (vehicleId: string) => byVehicle.get(vehicleId) ?? NO_KEYS };
  }, [summaries, snapshot]);
}
