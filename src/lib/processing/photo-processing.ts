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
 * Only a real, completed processor result is ever saved – together with the
 * processor's hints on it (`warnings`, e.g. "photo shows the other side"),
 * which processPhoto also returns. Any failure throws (with the processor's
 * German message where available) and saves no result; a failed job throws
 * ProcessingJobFailedError carrying the processor's errorCode (e.g. a
 * quality-gate rejection → "Foto neu aufnehmen"). A rejection that a new
 * photo fixes (quality-gate.ts needsRetake) is kept with the photo first
 * (data.recordPhotoRejection – best effort), so every device knows the shot
 * must be retaken.
 *
 * Resuming: `onSubmitted` reports the processor's job id right after the
 * submit (photo-checks.ts keeps it on the device), and `resumeJobId` waits
 * for such a job instead of sending the photo again – e.g. after the page
 * was reloaded or the tab discarded (the processor keeps jobs for 24 h). A
 * job the processor no longer knows (404: restarted, expired) is submitted
 * once more; a second 404 fails.
 */
import type { BackendMode, DataProvider } from "@/lib/data/types";
import type { ProcessingPresetId, ProcessingWarning, VehiclePhotoWithUrls } from "@/lib/domain/types";
import { AppError } from "@/lib/errors";
import * as client from "./processing-client";
import { loadOriginalPhoto, prepareProcessingUpload } from "./prepare-upload";
import { parseProcessingWarnings } from "./processing-warnings";
import { needsRetake, retakeFallbackMessage } from "./quality-gate";
import type { ProcessingJob, ProcessingJobStatus } from "./types";

export type PhotoStep = "preparing" | ProcessingJobStatus | "saving";

export interface PhotoProcessingDeps {
  mode: BackendMode;
  data: Pick<DataProvider, "saveProcessedPhoto" | "recordProcessedPhoto" | "recordPhotoRejection">;
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
  /** A job of this photo and style submitted earlier: its result is awaited instead of submitting again. */
  resumeJobId?: string | null;
  /** Called with the processor's job id right after the photo was submitted. */
  onSubmitted?: (jobId: string) => void;
}

export const NO_RESULT_MESSAGE = "Die Bildbearbeitung hat kein Ergebnis geliefert.";

/** A saved result. */
export interface PhotoProcessingResult {
  /** The processor's hints on the result (German messages), saved with it. */
  warnings: ProcessingWarning[];
}

/** The processor job ended as failed. `errorCode`: its reason (e.g. "vehicle_too_small"), if known. */
export class ProcessingJobFailedError extends AppError {
  constructor(
    readonly errorCode: string | null,
    userMessage: string,
  ) {
    super("unknown", { userMessage });
    this.name = "ProcessingJobFailedError";
  }
}

/** Shown when the processor rejected a photo without a text (never expected). */
const REJECTION_FALLBACK = "Bitte Foto neu aufnehmen.";

async function ensureCompleted(
  job: ProcessingJob,
  photo: VehiclePhotoWithUrls,
  data: PhotoProcessingDeps["data"],
): Promise<void> {
  if (job.status === "complete") return;
  const errorCode = job.errorCode ?? null;
  if (needsRetake(errorCode) && errorCode !== null) {
    // Kept with the photo for every device – never in the way of reporting the rejection.
    const message = job.error?.trim() || retakeFallbackMessage(errorCode) || REJECTION_FALLBACK;
    await data.recordPhotoRejection({ photoId: photo.id, errorCode, message }).catch(() => undefined);
  }
  throw new ProcessingJobFailedError(errorCode, job.error ?? "Bearbeitung fehlgeschlagen.");
}

export async function processPhoto(
  photo: VehiclePhotoWithUrls,
  preset: ProcessingPresetId,
  deps: PhotoProcessingDeps,
): Promise<PhotoProcessingResult> {
  let jobId = deps.resumeJobId ?? null;
  let resubmitted = false;
  for (;;) {
    if (jobId === null) {
      jobId = await submit(photo, preset, deps);
      deps.onSubmitted?.(jobId);
    }
    try {
      return await finish(photo, preset, jobId, deps);
    } catch (error) {
      // The processor no longer knows the job (restarted or expired): sent once more.
      if (!(error instanceof client.ProcessingJobNotFoundError) || resubmitted) throw error;
      resubmitted = true;
      jobId = null;
    }
  }
}

/** Sends the photo to the processor; returns the job id. */
async function submit(photo: VehiclePhotoWithUrls, preset: ProcessingPresetId, deps: PhotoProcessingDeps): Promise<string> {
  const api = deps.api ?? client;
  const options = { accessCode: deps.accessCode ?? null, signal: deps.signal };
  if (deps.mode === "supabase") {
    const { jobId, status } = await api.submitPhotoProcessing(
      { vehicleId: photo.vehicleId, photoId: photo.id, preset },
      options,
    );
    deps.onStep?.(status);
    return jobId;
  }
  deps.onStep?.("preparing");
  const original = await (deps.loadOriginal ?? loadOriginalPhoto)(photo.urls.original, deps.signal);
  const file = await (deps.prepareUpload ?? prepareProcessingUpload)(original);
  const { jobId, status } = await api.submitUploadProcessing({ file, preset, shotKey: photo.shotKey }, options);
  deps.onStep?.(status);
  return jobId;
}

/** Waits for the job and saves its result with the photo. */
async function finish(
  photo: VehiclePhotoWithUrls,
  preset: ProcessingPresetId,
  jobId: string,
  deps: PhotoProcessingDeps,
): Promise<PhotoProcessingResult> {
  const api = deps.api ?? client;
  const options = { accessCode: deps.accessCode ?? null, signal: deps.signal };
  const onUpdate = (job: ProcessingJob) => deps.onStep?.(job.status, job.progress);
  const job = await api.waitForProcessingJob(jobId, { ...options, onUpdate });
  await ensureCompleted(job, photo, deps.data);

  if (deps.mode === "supabase") {
    if (job.result?.kind !== "stored") throw new AppError("unknown", { userMessage: NO_RESULT_MESSAGE });
    const warnings = parseProcessingWarnings(job.warnings);
    deps.onStep?.("saving");
    await deps.data.recordProcessedPhoto({
      photoId: photo.id,
      preset,
      processedStoragePath: job.result.processedStoragePath,
      warnings,
    });
    return { warnings };
  }

  if (job.result?.kind !== "file") throw new AppError("unknown", { userMessage: NO_RESULT_MESSAGE });
  const warnings = parseProcessingWarnings(job.warnings);
  deps.onStep?.("saving");
  const result = await api.downloadProcessingResult(jobId, options);
  await deps.data.saveProcessedPhoto({ photo, preset, file: result, warnings });
  return { warnings };
}
