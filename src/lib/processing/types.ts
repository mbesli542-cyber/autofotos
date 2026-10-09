/**
 * Contract between the app and the image processing service (processor/).
 *
 * The browser only talks to the app's own API routes:
 *   GET  /api/processing-status          is a real processor connected?
 *   POST /api/process-photo              Supabase mode: processor reads the original from storage
 *   POST /api/process-upload             demo mode: the photo is uploaded (multipart)
 *   GET  /api/process-job/:jobId         job status (both modes)
 *   GET  /api/process-job/:jobId/result  demo mode: the processed JPEG
 * Server-side those routes delegate to an `ImageProcessor`
 * (`RealImageProcessor`, the only code that talks HTTP to the processor).
 * There is NO simulated processing: without a connected processor nothing
 * is processed or saved.
 */
import type { BackendMode } from "@/lib/data/types";
import { isProcessingPresetId, type ProcessingPresetId } from "@/lib/domain/types";

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

/** Job ids of the processor (`uuid4().hex`). Validated before forwarding. */
export const PROCESSING_JOB_ID_PATTERN = /^[a-f0-9]{32}$/;

export function isValidProcessingJobId(value: unknown): value is string {
  return typeof value === "string" && PROCESSING_JOB_ID_PATTERN.test(value);
}

/** POST /api/process-photo body (Supabase mode). */
export interface ProcessPhotoRequest {
  vehicleId: string;
  photoId: string;
  preset: ProcessingPresetId;
}

/** POST /api/process-photo and POST /api/process-upload response (202). */
export interface ProcessPhotoResponse {
  jobId: string;
  status: ProcessingJobStatus;
}

/**
 * What a finished job produced.
 * - `stored`: Supabase mode – the processor wrote the result to
 *   `vehicle-processed/...` and returns its storage path.
 * - `file`:   upload jobs (demo mode) – the processor keeps the JPEG; the
 *   browser downloads it via GET /api/process-job/:jobId/result (the
 *   processor's own `resultUrl` is never passed to the browser).
 */
export type ProcessingJobResult =
  | { kind: "stored"; processedStoragePath: string }
  | { kind: "file"; width: number | null; height: number | null; bytes: number | null };

export interface ProcessingWarning {
  code: string;
  /** German, user-presentable. */
  message: string;
}

/** GET /api/process-job/:jobId response. */
export interface ProcessingJob {
  jobId: string;
  /** null for upload jobs (demo mode). */
  vehicleId: string | null;
  photoId: string | null;
  preset: ProcessingPresetId | null;
  status: ProcessingJobStatus;
  /** 0..1 */
  progress: number;
  createdAt: string;
  updatedAt: string;
  result: ProcessingJobResult | null;
  /** German, user-presentable error message (the processor's own text). */
  error: string | null;
  /**
   * Machine-readable reason of a failed job (the processor's
   * `metadata.errorCode`, e.g. a quality-gate code like "vehicle_too_small",
   * see quality-gate.ts); null otherwise or when unknown.
   */
  errorCode: string | null;
  warnings: ProcessingWarning[];
}

export interface ProcessingContext {
  /** Authenticated user id (null in demo mode). */
  userId: string | null;
  /** Shot key of the photo, if the route could resolve it. */
  shotKey?: string;
}

/** POST {processor}/jobs/upload (demo mode). */
export interface UploadJobInput {
  file: Blob;
  fileName: string;
  preset: ProcessingPresetId;
  shotKey: string;
}

/**
 * Showroom state reported by the processor (/health and job metadata):
 * - "plates":   the complete set of eight angle-specific plates rendered from
 *               the AutoExperten 3D showroom – the ONLY usable state
 * - "missing":  the plate set is missing or unusable
 * - "master" / "fallback": older processors (single showroom photo /
 *               generated stand-in) – never accepted for processing
 */
export const SHOWROOM_SOURCES = ["plates", "missing", "master", "fallback"] as const;

export type ShowroomSource = (typeof SHOWROOM_SOURCES)[number];

/** The only showroom state real processing may run with. */
export const USABLE_SHOWROOM_SOURCE: ShowroomSource = "plates";

/** Parsed GET {processor}/health (requested with the Bearer key). */
export interface ProcessorHealthReport {
  /** The service answered with status "ok" and its model is usable. */
  ok: boolean;
  /** The detailed body was returned – i.e. the API key was accepted. */
  authorized: boolean;
  modelError: boolean;
  showroomSource: ShowroomSource | null;
  /** The showroom plate set exists but cannot be used (processor `showroomMasterError`). */
  showroomMasterError: boolean;
  /** The showroom preset (JSON) is broken. */
  presetError: boolean;
}

/** A processed JPEG streamed from the processor. */
export interface ProcessorResultFile {
  body: ReadableStream<Uint8Array>;
  contentLength: string | null;
}

export interface ImageProcessor {
  readonly name: string;
  /** Supabase contract: the processor reads the original from storage. */
  submit(request: ProcessPhotoRequest, context: ProcessingContext): Promise<ProcessingJob>;
  /** Demo mode: the photo itself is uploaded. */
  submitUpload(input: UploadJobInput, signal?: AbortSignal): Promise<ProcessingJob>;
  /** null → unknown job (404). */
  getJob(jobId: string): Promise<ProcessingJob | null>;
  /** null → unknown job (404). Throws for 409 (not ready) and other errors. */
  fetchResult(jobId: string, signal?: AbortSignal): Promise<ProcessorResultFile | null>;
  /** null → not reachable / no usable answer. */
  getHealth(timeoutMs?: number): Promise<ProcessorHealthReport | null>;
}

/** GET /api/processing-status response. */
export interface ProcessingStatus {
  /** A real processor answers (key accepted, model usable). */
  connected: boolean;
  /** IMAGE_PROCESSOR: "mock" = no processor connected (there are no simulated results). */
  processor: "real" | "mock";
  showroomSource: ShowroomSource | null;
  /** German message when the showroom is not ready (e.g. plate set missing). */
  showroomError: string | null;
  /** Demo mode with PROCESSING_ACCESS_CODE set. */
  accessCodeRequired: boolean;
  dataBackend: BackendMode;
}

/** Processing can only start against a connected processor with the complete plate set. */
export function canStartProcessing(status: ProcessingStatus | null): boolean {
  return Boolean(
    status &&
      status.connected &&
      status.showroomSource === USABLE_SHOWROOM_SOURCE &&
      status.showroomError === null,
  );
}

export interface ApiErrorBody {
  error: { code: string; message: string };
}

/* ------------------------------------------------------------------------ */
/* Error codes, messages, limits                                             */
/* ------------------------------------------------------------------------ */

/** Error code when the processor queue is full (503) – the client retries. */
export const PROCESSING_BUSY_CODE = "processing_busy";

/** Error code (503) when no real processor is configured or reachable. */
export const PROCESSING_NOT_CONNECTED_CODE = "processing_not_connected";
export const PROCESSING_NOT_CONNECTED_MESSAGE = "Echte Showroom-Bearbeitung ist noch nicht verbunden.";

export const SHOWROOM_MASTER_MISSING_MESSAGE = "AutoExperten Showroom-Master fehlt.";
export const SHOWROOM_CONFIG_ERROR_MESSAGE =
  "Der AutoExperten Showroom ist auf dem Server nicht richtig eingerichtet.";

/** Demo mode with PROCESSING_ACCESS_CODE: header carrying the (URI-encoded) code. */
export const PROCESSING_ACCESS_CODE_HEADER = "x-processing-access-code";
/** 401 error code when the access code is missing or wrong. */
export const ACCESS_CODE_REQUIRED_CODE = "access_code_required";

/**
 * Upload limits (demo mode). Vercel functions accept request bodies up to
 * 4.5 MB – the server accepts files up to 4.4 MB, the browser targets 4.0 MB.
 */
export const PROCESS_UPLOAD_MAX_BYTES = 4_400_000;
export const PROCESS_UPLOAD_TARGET_BYTES = 4_000_000;
/** Longest edge the browser downscales uploads to (the processor outputs ≤ 3200 px). */
export const PROCESS_UPLOAD_MAX_LONG_EDGE = 3200;

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
