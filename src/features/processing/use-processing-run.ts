"use client";

import { useCallback, useRef, useState } from "react";
import { getAppServices } from "@/lib/app-services";
import type { ProcessingPresetId, VehiclePhotoWithUrls } from "@/lib/domain/types";
import { toUserMessage } from "@/lib/errors";
import {
  ProcessingJobFailedError,
  processPhoto,
  type PhotoStep,
} from "@/lib/processing/photo-processing";
import { AccessCodeRequiredError } from "@/lib/processing/processing-client";
import {
  getShotTreatment,
  selectPhotosForProcessing,
  type ShotTreatment,
} from "@/lib/processing/shot-treatment";
import { markProcessedIfDone } from "@/lib/workflow/vehicle-workflow";

export type PhotoRunStatus = "waiting" | PhotoStep;

export interface PhotoRunState {
  photoId: string;
  shotKey: string;
  title: string;
  shotOrder: number;
  treatment: ShotTreatment;
  status: PhotoRunStatus;
  progress: number;
  /** German message (the processor's own text where available). */
  error: string | null;
  /** Processor reason of a failed job (e.g. a quality-gate code), see quality-gate.ts. */
  errorCode: string | null;
}

export type RunPhase = "idle" | "running" | "done";

export type RunOutcome = "done" | "access_denied" | "nothing_to_do";

export interface RunResult {
  outcome: RunOutcome;
  message?: string;
}

const CONCURRENCY = 3;

const GENERIC_FAILURE = "Bearbeitung fehlgeschlagen.";

/**
 * Runs real processing for the template shots of a vehicle (extra photos
 * are skipped). The adapter follows the data backend (Supabase contract or
 * demo upload); only real results are saved, failures save nothing.
 *
 * Shots the processor rejected (e.g. quality gate) stay failed while the rest
 * of the run continues; after a retake, `retry` processes just the new photo
 * of that shot.
 */
export function useProcessingRun(vehicleId: string) {
  const [phase, setPhase] = useState<RunPhase>("idle");
  const [items, setItems] = useState<PhotoRunState[]>([]);
  /** Runs (`start` / `retry`) in flight – the phase is "done" once all ended. */
  const activeRuns = useRef(0);
  /** Bumped by `start` / `reset` so a retry of an older run cannot revive it. */
  const generation = useRef(0);

  const update = useCallback((photoId: string, patch: Partial<PhotoRunState>) => {
    setItems((current) => current.map((item) => (item.photoId === photoId ? { ...item, ...patch } : item)));
  }, []);

  /** Processes one photo and records its outcome; throws only AccessCodeRequiredError. */
  const processOne = useCallback(
    async (
      photo: VehiclePhotoWithUrls,
      preset: ProcessingPresetId,
      accessCode: string | null,
      signal: AbortSignal,
    ): Promise<void> => {
      const { backend } = getAppServices();
      try {
        await processPhoto(photo, preset, {
          mode: backend.mode,
          data: backend.data,
          accessCode,
          signal,
          onStep: (status, progress) =>
            update(photo.id, progress === undefined ? { status } : { status, progress }),
        });
        update(photo.id, { status: "complete", progress: 1, error: null, errorCode: null });
      } catch (error) {
        if (error instanceof AccessCodeRequiredError) throw error;
        update(photo.id, {
          status: "failed",
          error: toUserMessage(error, GENERIC_FAILURE),
          errorCode: error instanceof ProcessingJobFailedError ? error.errorCode : null,
        });
      }
    },
    [update],
  );

  /** Ends one run; the last one to finish syncs the vehicle status and shows the summary. */
  const finishRun = useCallback(
    async (runGeneration: number) => {
      // A run of an older generation (before start/reset) no longer counts.
      if (runGeneration !== generation.current) return;
      activeRuns.current -= 1;
      if (activeRuns.current > 0) return;
      const { backend, template } = getAppServices();
      try {
        // Only when every required shot really has a processed version.
        await markProcessedIfDone(backend.data, template, vehicleId);
      } catch {
        // Status update is retried on the next run.
      }
      if (runGeneration === generation.current) setPhase("done");
    },
    [vehicleId],
  );

  const start = useCallback(
    async (
      photos: readonly VehiclePhotoWithUrls[],
      preset: ProcessingPresetId,
      accessCode: string | null,
    ): Promise<RunResult> => {
      const { template } = getAppServices();
      const selected = selectPhotosForProcessing(template, photos);
      if (selected.length === 0) return { outcome: "nothing_to_do" };

      generation.current += 1;
      const runGeneration = generation.current;
      activeRuns.current = 1;
      setPhase("running");
      setItems(
        selected.map((photo) => ({
          photoId: photo.id,
          shotKey: photo.shotKey,
          title: photo.title,
          shotOrder: photo.shotOrder,
          treatment: getShotTreatment(template, photo.shotKey) ?? "original_environment",
          status: "waiting",
          progress: 0,
          error: null,
          errorCode: null,
        })),
      );

      const controller = new AbortController();
      let accessDenied: string | null = null;

      const queue = [...selected];
      const worker = async () => {
        for (let photo = queue.shift(); photo && !controller.signal.aborted; photo = queue.shift()) {
          try {
            await processOne(photo, preset, accessCode, controller.signal);
          } catch (error) {
            accessDenied ??= toUserMessage(error);
            controller.abort(); // stop the other uploads – the code is wrong for all of them
          }
        }
      };
      await Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, worker));

      if (accessDenied) {
        activeRuns.current = 0;
        generation.current += 1;
        setItems([]);
        setPhase("idle");
        return { outcome: "access_denied", message: accessDenied };
      }
      await finishRun(runGeneration);
      return { outcome: "done" };
    },
    [finishRun, processOne],
  );

  /**
   * Processes the new photo of a shot that failed before (after "Foto neu
   * aufnehmen"); the run's other results stay as they are.
   */
  const retry = useCallback(
    async (
      photo: VehiclePhotoWithUrls,
      preset: ProcessingPresetId,
      accessCode: string | null,
    ): Promise<RunResult> => {
      const runGeneration = generation.current;
      activeRuns.current = Math.max(0, activeRuns.current) + 1;
      setPhase("running");
      setItems((current) =>
        current.map((item) =>
          item.shotKey === photo.shotKey
            ? { ...item, photoId: photo.id, title: photo.title, status: "waiting", progress: 0, error: null, errorCode: null }
            : item,
        ),
      );
      try {
        await processOne(photo, preset, accessCode, new AbortController().signal);
      } catch (error) {
        // The stored access code is no longer valid: back to the start screen.
        activeRuns.current = 0;
        generation.current += 1;
        setItems([]);
        setPhase("idle");
        return { outcome: "access_denied", message: toUserMessage(error) };
      }
      await finishRun(runGeneration);
      return { outcome: "done" };
    },
    [finishRun, processOne],
  );

  const reset = useCallback(() => {
    generation.current += 1;
    activeRuns.current = 0;
    setPhase("idle");
    setItems([]);
  }, []);

  return { phase, items, start, retry, reset };
}
