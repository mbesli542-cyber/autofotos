/**
 * Defensive parsing of the processor's HTTP responses (pure, server side).
 *
 * Processor contract (processor/app/main.py):
 *   JobResponse { jobId, vehicleId, photoId, preset, status, progress,
 *                 createdAt, updatedAt, result, error, warnings, metadata }
 *     result: { kind: "stored", processedStoragePath }                  (POST /jobs)
 *           | { kind: "file", resultUrl, width, height, bytes }         (POST /jobs/upload)
 *   GET /health (with Bearer) → { status, version, modelLoaded, modelError,
 *                 showroomSource, showroomMasterError, presetError, … }
 *
 * The app-facing ProcessingJob never contains the processor's `resultUrl`,
 * and a job composited onto the emergency fallback showroom is never
 * reported as complete – it is not the AutoExperten design.
 */
import { isProcessingPresetId } from "@/lib/domain/types";
import {
  PROCESSING_JOB_STATUSES,
  SHOWROOM_MASTER_MISSING_MESSAGE,
  isValidProcessingJobId,
  type ProcessingJob,
  type ProcessingJobResult,
  type ProcessingJobStatus,
  type ProcessingWarning,
  type ProcessorHealthReport,
  type ShowroomSource,
} from "./types";

const MAX_MESSAGE_LENGTH = 300;
export const MISSING_RESULT_MESSAGE = "Die Bildbearbeitung hat kein Ergebnis geliefert.";
export const GENERIC_JOB_ERROR_MESSAGE = "Bearbeitung fehlgeschlagen.";

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asCount(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.round(value) : null;
}

/** A short user-presentable text from the processor, or null. */
export function asMessage(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  return text.length > 0 && text.length <= MAX_MESSAGE_LENGTH ? text : null;
}

function parseShowroomSource(value: unknown): ShowroomSource | null {
  return value === "master" || value === "fallback" ? value : null;
}

function parseResult(value: unknown): ProcessingJobResult | null {
  const result = asRecord(value);
  if (!result) return null;
  if (result.kind === "stored" && typeof result.processedStoragePath === "string" && result.processedStoragePath) {
    return { kind: "stored", processedStoragePath: result.processedStoragePath };
  }
  if (result.kind === "file") {
    // `resultUrl` is deliberately dropped – the browser uses the app's own route.
    return {
      kind: "file",
      width: asCount(result.width),
      height: asCount(result.height),
      bytes: asCount(result.bytes),
    };
  }
  return null;
}

function parseWarnings(value: unknown): ProcessingWarning[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const warning = asRecord(item);
    const message = asMessage(warning?.message);
    if (!warning || !message) return [];
    return [{ code: asString(warning.code, "warning").slice(0, 40), message }];
  });
}

/** True when the processor reports that the emergency fallback showroom was used. */
function usedFallbackShowroom(metadata: Record<string, unknown> | null): boolean {
  if (!metadata) return false;
  return metadata.showroomPlaceholder === true || parseShowroomSource(metadata.showroomSource) === "fallback";
}

/**
 * Processor JobResponse → app ProcessingJob. Returns null if the response is
 * unusable (wrong job id format, unknown status).
 */
export function parseProcessorJob(value: unknown): ProcessingJob | null {
  const job = asRecord(value);
  if (!job || !isValidProcessingJobId(job.jobId)) return null;
  if (!PROCESSING_JOB_STATUSES.includes(job.status as ProcessingJobStatus)) return null;

  let status = job.status as ProcessingJobStatus;
  let result = status === "complete" ? parseResult(job.result) : null;
  let error = asMessage(job.error);
  const progress = typeof job.progress === "number" && Number.isFinite(job.progress) ? job.progress : 0;

  if (status === "complete") {
    if (usedFallbackShowroom(asRecord(job.metadata))) {
      status = "failed";
      result = null;
      error = SHOWROOM_MASTER_MISSING_MESSAGE;
    } else if (!result) {
      status = "failed";
      error = MISSING_RESULT_MESSAGE;
    }
  }
  if (status === "failed") {
    result = null;
    error ??= GENERIC_JOB_ERROR_MESSAGE;
  } else if (status !== "complete") {
    error = null;
  }

  return {
    jobId: job.jobId,
    vehicleId: typeof job.vehicleId === "string" ? job.vehicleId : null,
    photoId: typeof job.photoId === "string" ? job.photoId : null,
    preset: isProcessingPresetId(job.preset) ? job.preset : null,
    status,
    progress: status === "complete" ? 1 : Math.min(1, Math.max(0, progress)),
    createdAt: asString(job.createdAt),
    updatedAt: asString(job.updatedAt),
    result,
    error,
    warnings: parseWarnings(job.warnings),
  };
}

/**
 * GET {processor}/health → report. `httpStatus` 503 means the segmentation
 * model cannot be loaded. Without a valid key the processor only returns
 * `{ status, version }` – then `authorized` is false (every job would be
 * rejected with 401). Returns null for unusable answers.
 */
export function parseProcessorHealth(httpStatus: number, value: unknown): ProcessorHealthReport | null {
  const health = asRecord(value);
  if (!health || (health.status !== "ok" && health.status !== "error")) return null;
  const authorized = "showroomSource" in health || "modelLoaded" in health;
  const modelError = health.modelError === true || httpStatus === 503;
  return {
    ok: httpStatus === 200 && health.status === "ok" && !modelError,
    authorized,
    modelError,
    showroomSource:
      health.showroomPlaceholder === true ? "fallback" : parseShowroomSource(health.showroomSource),
    showroomMasterError: typeof health.showroomMasterError === "string" && health.showroomMasterError.length > 0,
    presetError: typeof health.presetError === "string" && health.presetError.length > 0,
  };
}
