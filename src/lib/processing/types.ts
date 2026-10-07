/**
 * Contract between the app and the (future) image processing service.
 *
 * The UI only talks to `/api/process-photo` and `/api/process-job/:jobId`.
 * Server-side, those routes delegate to an `ImageProcessor`. Swapping the
 * MockImageProcessor for a RealImageProcessor does not change the UI.
 */
import {
  isProcessingPresetId,
  type ProcessingPresetId,
} from "@/lib/domain/types";

export const PROCESSING_JOB_STATUSES = [
  "queued",
  "processing",
  "complete",
  "failed",
] as const;

export type ProcessingJobStatus = (typeof PROCESSING_JOB_STATUSES)[number];

export const PROCESSING_JOB_STATUS_LABELS: Record<ProcessingJobStatus, string> = {
  queued: "Wartend",
  processing: "In Bearbeitung",
  complete: "Fertig",
  failed: "Fehlgeschlagen",
};

export function isTerminalJobStatus(status: ProcessingJobStatus): boolean {
  return status === "complete" || status === "failed";
}

/** POST /api/process-photo body. */
export interface ProcessPhotoRequest {
  vehicleId: string;
  photoId: string;
  preset: ProcessingPresetId;
}

/** POST /api/process-photo response. */
export interface ProcessPhotoResponse {
  jobId: string;
  status: ProcessingJobStatus;
}

/**
 * What a finished job produced.
 * - `stored`: the processing service wrote the result to
 *   `vehicle-processed/...` and returns its storage path.
 * - `mock_preview`: the mock processor produced no file; the client renders
 *   a clearly marked branded preview (original pixels untouched).
 */
export type ProcessingJobResult =
  | { kind: "stored"; processedStoragePath: string }
  | { kind: "mock_preview" };

/** GET /api/process-job/:jobId response. */
export interface ProcessingJob {
  jobId: string;
  vehicleId: string;
  photoId: string;
  preset: ProcessingPresetId;
  status: ProcessingJobStatus;
  /** 0..1 */
  progress: number;
  createdAt: string;
  updatedAt: string;
  result: ProcessingJobResult | null;
  /** German, user-presentable error message. */
  error: string | null;
}

export interface ProcessingContext {
  /** Authenticated user id (null in demo mode). */
  userId: string | null;
  /** Shot key of the photo, if the route could resolve it. */
  shotKey?: string;
}

export interface ImageProcessor {
  readonly name: string;
  submit(request: ProcessPhotoRequest, context: ProcessingContext): Promise<ProcessingJob>;
  getJob(jobId: string, context: ProcessingContext): Promise<ProcessingJob | null>;
}

export interface ApiErrorBody {
  error: { code: string; message: string };
}

/** Error code of POST /api/process-photo when the processor queue is full (503). */
export const PROCESSING_BUSY_CODE = "processing_busy";

const ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

export function parseProcessPhotoRequest(
  body: unknown,
): { ok: true; value: ProcessPhotoRequest } | { ok: false; message: string } {
  if (typeof body !== "object" || body === null) {
    return { ok: false, message: "Ungültige Anfrage." };
  }
  const { vehicleId, photoId, preset } = body as Record<string, unknown>;
  if (typeof vehicleId !== "string" || !ID_PATTERN.test(vehicleId)) {
    return { ok: false, message: "Ungültige Fahrzeug-ID." };
  }
  if (typeof photoId !== "string" || !ID_PATTERN.test(photoId)) {
    return { ok: false, message: "Ungültige Foto-ID." };
  }
  if (!isProcessingPresetId(preset)) {
    return { ok: false, message: "Unbekannter Bearbeitungsstil." };
  }
  return { ok: true, value: { vehicleId, photoId, preset } };
}
