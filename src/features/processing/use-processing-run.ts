"use client";

import { useCallback, useState } from "react";
import { getAppServices } from "@/lib/app-services";
import type { ProcessingPresetId, VehiclePhotoWithUrls } from "@/lib/domain/types";
import { AppError, toUserMessage } from "@/lib/errors";
import { renderMockProcessedPreview } from "@/lib/processing/mock-preview-renderer";
import { submitPhotoProcessing, waitForProcessingJob } from "@/lib/processing/processing-client";
import type { ProcessingJobStatus } from "@/lib/processing/types";
import { markProcessedIfDone } from "@/lib/workflow/vehicle-workflow";

export type PhotoRunStatus = "waiting" | ProcessingJobStatus | "saving";

export interface PhotoRunState {
  photoId: string;
  title: string;
  shotOrder: number;
  status: PhotoRunStatus;
  progress: number;
  error: string | null;
}

export type RunPhase = "idle" | "running" | "done";

const CONCURRENCY = 3;

/**
 * Runs processing for a set of photos through the processing API.
 * UI code is independent of whether a mock or real processor answers.
 */
export function useProcessingRun(vehicleId: string) {
  const [phase, setPhase] = useState<RunPhase>("idle");
  const [items, setItems] = useState<PhotoRunState[]>([]);

  const update = useCallback((photoId: string, patch: Partial<PhotoRunState>) => {
    setItems((current) => current.map((item) => (item.photoId === photoId ? { ...item, ...patch } : item)));
  }, []);

  const start = useCallback(
    async (photos: readonly VehiclePhotoWithUrls[], preset: ProcessingPresetId) => {
      const { backend, template } = getAppServices();
      setPhase("running");
      setItems(
        photos.map((photo) => ({
          photoId: photo.id,
          title: photo.title,
          shotOrder: photo.shotOrder,
          status: "waiting",
          progress: 0,
          error: null,
        })),
      );

      const processOne = async (photo: VehiclePhotoWithUrls) => {
        try {
          const { jobId, status } = await submitPhotoProcessing({
            vehicleId,
            photoId: photo.id,
            preset,
          });
          update(photo.id, { status });
          const job = await waitForProcessingJob(jobId, {
            onUpdate: (next) => update(photo.id, { status: next.status, progress: next.progress }),
          });
          if (job.status === "failed" || !job.result) {
            throw new AppError("unknown", {
              userMessage: job.error ?? "Bearbeitung fehlgeschlagen.",
            });
          }
          update(photo.id, { status: "saving" });
          if (job.result.kind === "mock_preview") {
            // Simulation: separate preview file, original stays untouched.
            const file = await renderMockProcessedPreview(photo.urls.original, preset);
            await backend.data.saveProcessedPhoto({ photo, preset, file });
          } else {
            await backend.data.recordProcessedPhoto({
              photoId: photo.id,
              preset,
              processedStoragePath: job.result.processedStoragePath,
            });
          }
          update(photo.id, { status: "complete", progress: 1 });
        } catch (error) {
          update(photo.id, {
            status: "failed",
            error: toUserMessage(error, "Bearbeitung fehlgeschlagen."),
          });
        }
      };

      const queue = [...photos];
      const worker = async () => {
        for (let photo = queue.shift(); photo; photo = queue.shift()) {
          await processOne(photo);
        }
      };
      await Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, worker));

      try {
        await markProcessedIfDone(backend.data, template, vehicleId);
      } catch {
        // Status update is retried on the next run.
      }
      setPhase("done");
    },
    [update, vehicleId],
  );

  const reset = useCallback(() => {
    setPhase("idle");
    setItems([]);
  }, []);

  return { phase, items, start, reset };
}
