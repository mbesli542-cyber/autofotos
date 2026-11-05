"use client";

import { useCallback, useEffect, useEffectEvent, useSyncExternalStore } from "react";
import type { ProcessingPresetId, VehiclePhotoWithUrls } from "@/lib/domain/types";
import {
  IDLE_RUN,
  processingRuns,
  type ProcessingRunSnapshot,
  type ResumeResult,
  type StartOptions,
} from "@/lib/processing/processing-runs";

export type {
  PhotoRunState,
  PhotoRunStatus,
  ResumeResult,
  RunOutcome,
  RunPhase,
  RunResult,
  StartOptions,
} from "@/lib/processing/processing-runs";

const getServerSnapshot = (): ProcessingRunSnapshot => IDLE_RUN;

/**
 * The processing run of a vehicle (src/lib/processing/processing-runs.ts).
 * The run lives outside the component, so a re-created screen shows the
 * run still in progress (or its result) and cannot start it a second time.
 * A run that was going on before the page was reloaded continues when the
 * screen is shown (`processingRuns.resume`); `onResumed` gets its outcome
 * (e.g. the stored access code was refused – the screen asks for it again).
 */
export function useProcessingRun(vehicleId: string, onResumed?: (result: ResumeResult) => void) {
  const getSnapshot = useCallback(() => processingRuns.getSnapshot(vehicleId), [vehicleId]);
  const { phase, items, preset } = useSyncExternalStore(processingRuns.subscribe, getSnapshot, getServerSnapshot);

  const resumed = useEffectEvent((result: ResumeResult) => onResumed?.(result));
  useEffect(() => {
    void processingRuns.resume(vehicleId).then((result) => {
      if (result) resumed(result);
    });
  }, [vehicleId]);

  const start = useCallback(
    (
      photos: readonly VehiclePhotoWithUrls[],
      runPreset: ProcessingPresetId,
      accessCode: string | null,
      options?: StartOptions,
    ) => processingRuns.start(vehicleId, photos, runPreset, accessCode, options),
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
