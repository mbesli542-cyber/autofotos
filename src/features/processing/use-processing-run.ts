"use client";

import { useCallback, useSyncExternalStore } from "react";
import type { ProcessingPresetId, VehiclePhotoWithUrls } from "@/lib/domain/types";
import { IDLE_RUN, processingRuns, type ProcessingRunSnapshot } from "@/lib/processing/processing-runs";

export type {
  PhotoRunState,
  PhotoRunStatus,
  RunOutcome,
  RunPhase,
  RunResult,
} from "@/lib/processing/processing-runs";

const getServerSnapshot = (): ProcessingRunSnapshot => IDLE_RUN;

/**
 * The processing run of a vehicle (src/lib/processing/processing-runs.ts).
 * The run lives outside the component, so a re-created screen shows the
 * run still in progress (or its result) and cannot start it a second time.
 */
export function useProcessingRun(vehicleId: string) {
  const getSnapshot = useCallback(() => processingRuns.getSnapshot(vehicleId), [vehicleId]);
  const { phase, items, preset } = useSyncExternalStore(processingRuns.subscribe, getSnapshot, getServerSnapshot);

  const start = useCallback(
    (photos: readonly VehiclePhotoWithUrls[], runPreset: ProcessingPresetId, accessCode: string | null) =>
      processingRuns.start(vehicleId, photos, runPreset, accessCode),
    [vehicleId],
  );
  const retry = useCallback(
    (photos: readonly VehiclePhotoWithUrls[], runPreset: ProcessingPresetId, accessCode: string | null) =>
      processingRuns.retry(vehicleId, photos, runPreset, accessCode),
    [vehicleId],
  );
  const reset = useCallback(() => processingRuns.reset(vehicleId), [vehicleId]);

  return { phase, items, preset, start, retry, reset };
}
