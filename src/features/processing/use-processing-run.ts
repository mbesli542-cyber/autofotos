"use client";

import { useCallback, useState } from "react";
import { getAppServices } from "@/lib/app-services";
import type { ProcessingPresetId, VehiclePhotoWithUrls } from "@/lib/domain/types";
import { toUserMessage } from "@/lib/errors";
import { processPhoto, type PhotoStep } from "@/lib/processing/photo-processing";
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
  title: string;
  shotOrder: number;
  treatment: ShotTreatment;
  status: PhotoRunStatus;
  progress: number;
  /** German message (the processor's own text where available). */
  error: string | null;
}

export type RunPhase = "idle" | "running" | "done";

export type RunOutcome = "done" | "access_denied" | "nothing_to_do";

const CONCURRENCY = 3;

/**
 * Runs real processing for the template shots of a vehicle (extra photos
 * are skipped). The adapter follows the data backend (Supabase contract or
 * demo upload); only real results are saved, failures save nothing.
 */
export function useProcessingRun(vehicleId: string) {
  const [phase, setPhase] = useState<RunPhase>("idle");
  const [items, setItems] = useState<PhotoRunState[]>([]);

  const update = useCallback((photoId: string, patch: Partial<PhotoRunState>) => {
    setItems((current) => current.map((item) => (item.photoId === photoId ? { ...item, ...patch } : item)));
  }, []);

  const start = useCallback(
    async (
      photos: readonly VehiclePhotoWithUrls[],
      preset: ProcessingPresetId,
      accessCode: string | null,
    ): Promise<{ outcome: RunOutcome; message?: string }> => {
      const { backend, template } = getAppServices();
      const selected = selectPhotosForProcessing(template, photos);
      if (selected.length === 0) return { outcome: "nothing_to_do" };

      setPhase("running");
      setItems(
        selected.map((photo) => ({
          photoId: photo.id,
          title: photo.title,
          shotOrder: photo.shotOrder,
          treatment: getShotTreatment(template, photo.shotKey) ?? "original_environment",
          status: "waiting",
          progress: 0,
          error: null,
        })),
      );

      const controller = new AbortController();
      let accessDenied: string | null = null;

      const processOne = async (photo: VehiclePhotoWithUrls) => {
        try {
          await processPhoto(photo, preset, {
            mode: backend.mode,
            data: backend.data,
            accessCode,
            signal: controller.signal,
            onStep: (status, progress) =>
              update(photo.id, progress === undefined ? { status } : { status, progress }),
          });
          update(photo.id, { status: "complete", progress: 1, error: null });
        } catch (error) {
          if (error instanceof AccessCodeRequiredError) {
            accessDenied ??= toUserMessage(error);
            controller.abort(); // stop the other uploads – the code is wrong for all of them
          }
          if (accessDenied) return;
          update(photo.id, { status: "failed", error: toUserMessage(error, "Bearbeitung fehlgeschlagen.") });
        }
      };

      const queue = [...selected];
      const worker = async () => {
        for (let photo = queue.shift(); photo && !controller.signal.aborted; photo = queue.shift()) {
          await processOne(photo);
        }
      };
      await Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, worker));

      if (accessDenied) {
        setItems([]);
        setPhase("idle");
        return { outcome: "access_denied", message: accessDenied };
      }

      try {
        // Only when every required shot really has a processed version.
        await markProcessedIfDone(backend.data, template, vehicleId);
      } catch {
        // Status update is retried on the next run.
      }
      setPhase("done");
      return { outcome: "done" };
    },
    [update, vehicleId],
  );

  const reset = useCallback(() => {
    setPhase("idle");
    setItems([]);
  }, []);

  return { phase, items, start, reset };
}
