/**
 * Processes ONE photo with the real processor and saves the result – the two
 * adapters, chosen by the data backend:
 *
 * - supabase: POST /api/process-photo → poll → result { kind: "stored" }
 *             → data.recordProcessedPhoto (the processor stored the file)
 * - demo:     original Blob (IndexedDB) → downscaled copy → POST /api/process-upload
 *             → poll → result { kind: "file" } → GET /api/process-job/:id/result
 *             → data.saveProcessedPhoto (stored as a separate file in IndexedDB)
 *
 * Only a real, completed processor result is ever saved. Any failure throws
 * (with the processor's German message where available) and saves nothing.
 */
import type { BackendMode, DataProvider } from "@/lib/data/types";
import type { ProcessingPresetId, VehiclePhotoWithUrls } from "@/lib/domain/types";
import { AppError } from "@/lib/errors";
import * as client from "./processing-client";
import { loadOriginalPhoto, prepareProcessingUpload } from "./prepare-upload";
import type { ProcessingJob, ProcessingJobStatus } from "./types";

export type PhotoStep = "preparing" | ProcessingJobStatus | "saving";

export interface PhotoProcessingDeps {
  mode: BackendMode;
  data: Pick<DataProvider, "saveProcessedPhoto" | "recordProcessedPhoto">;
  accessCode?: string | null;
  signal?: AbortSignal;
  onStep?: (step: PhotoStep, progress?: number) => void;
  /** Injectable for tests. */
  api?: Pick<
    typeof client,
    "submitPhotoProcessing" | "submitUploadProcessing" | "waitForProcessingJob" | "downloadProcessingResult"
  >;
  loadOriginal?: (url: string, signal?: AbortSignal) => Promise<Blob>;
  prepareUpload?: (original: Blob) => Promise<Blob>;
}

export const NO_RESULT_MESSAGE = "Die Bildbearbeitung hat kein Ergebnis geliefert.";

function ensureCompleted(job: ProcessingJob): void {
  if (job.status !== "complete") {
    throw new AppError("unknown", { userMessage: job.error ?? "Bearbeitung fehlgeschlagen." });
  }
}

export async function processPhoto(
  photo: VehiclePhotoWithUrls,
  preset: ProcessingPresetId,
  deps: PhotoProcessingDeps,
): Promise<void> {
  const api = deps.api ?? client;
  const options = { accessCode: deps.accessCode ?? null, signal: deps.signal };
  const onUpdate = (job: ProcessingJob) => deps.onStep?.(job.status, job.progress);

  if (deps.mode === "supabase") {
    const { jobId, status } = await api.submitPhotoProcessing(
      { vehicleId: photo.vehicleId, photoId: photo.id, preset },
      options,
    );
    deps.onStep?.(status);
    const job = await api.waitForProcessingJob(jobId, { ...options, onUpdate });
    ensureCompleted(job);
    if (job.result?.kind !== "stored") throw new AppError("unknown", { userMessage: NO_RESULT_MESSAGE });
    deps.onStep?.("saving");
    await deps.data.recordProcessedPhoto({
      photoId: photo.id,
      preset,
      processedStoragePath: job.result.processedStoragePath,
    });
    return;
  }

  deps.onStep?.("preparing");
  const original = await (deps.loadOriginal ?? loadOriginalPhoto)(photo.urls.original, deps.signal);
  const file = await (deps.prepareUpload ?? prepareProcessingUpload)(original);
  const { jobId, status } = await api.submitUploadProcessing({ file, preset, shotKey: photo.shotKey }, options);
  deps.onStep?.(status);
  const job = await api.waitForProcessingJob(jobId, { ...options, onUpdate });
  ensureCompleted(job);
  if (job.result?.kind !== "file") throw new AppError("unknown", { userMessage: NO_RESULT_MESSAGE });
  deps.onStep?.("saving");
  const result = await api.downloadProcessingResult(jobId, options);
  await deps.data.saveProcessedPhoto({ photo, preset, file: result });
}
