"use client";

import { useEffect, useMemo, useSyncExternalStore } from "react";
import { getShotTemplate } from "@/lib/app-services";
import type { VehicleSummary } from "@/lib/domain/types";
import { photoChecks } from "@/lib/processing/photo-checks";
import { countVehicleRetakes } from "@/lib/processing/retake-summary";

/**
 * Shots to retake per vehicle id (vehicle lists: "Neu aufnehmen" badge) –
 * from the automatic checks of this device (rejections are kept across
 * reloads) and the hints on the saved results. Starts the checks' store
 * only; it never asks the processor.
 */
export function useVehicleRetakeCounts(summaries: readonly VehicleSummary[]): ReadonlyMap<string, number> {
  useEffect(() => {
    photoChecks.start();
  }, []);
  const snapshot = useSyncExternalStore(photoChecks.subscribe, photoChecks.getSnapshot, photoChecks.getServerSnapshot);
  return useMemo(() => {
    const template = getShotTemplate();
    return new Map(
      summaries.map((summary) => [
        summary.vehicle.id,
        countVehicleRetakes(template, summary.vehicle.id, summary.photos, snapshot),
      ]),
    );
  }, [summaries, snapshot]);
}
