/**
 * HTTP contract of the Python image processor (processor/) as used by the
 * developer test page `/dev/processing-test`.
 *
 *   POST /jobs/upload               multipart: file, preset, shotKey → 202 ProcessorJob
 *   GET  /jobs/{jobId}              → 200 ProcessorJob | 404 ProcessorErrorBody
 *   GET  /jobs/{jobId}/result       → 200 image/jpeg | 404 | 409 (not complete yet)
 *   GET  /jobs/{jobId}/debug/{name} → file (only with PROCESSOR_DEBUG=true, else 404)
 *   GET  /showroom/{preset}.jpg?width=N
 *                                   → 200 image/jpeg (branded showroom background
 *                                     without vehicle, 4:3), header X-Showroom-Source;
 *                                     404 unknown preset, 400 invalid width
 *   GET  /health                    → ProcessorHealth
 *   Auth: `Authorization: Bearer <IMAGE_PROCESSING_API_KEY>` (only if configured)
 *
 * The browser never talks to the processor directly – it only calls the
 * proxy routes under `/api/dev/processing-test` (see DEV_TEST_API).
 */
import { PROCESSING_JOB_STATUSES, type ProcessingJobStatus } from "./types";

/* ------------------------------------------------------------------------ */
/* Contract types                                                            */
/* ------------------------------------------------------------------------ */

export interface ProcessorErrorBody {
  error: { code: string; message: string };
}

export type ProcessorJobResult =
  /** Result written to Supabase storage (`vehicle-processed/...`). */
  | { kind: "stored"; processedStoragePath: string }
  /** Result kept by the processor, downloadable via GET /jobs/{id}/result. */
  | { kind: "file"; resultUrl: string; width: number; height: number; bytes: number };

/**
 * Which showroom background the processor composited onto:
 * - "master":   the final AutoExperten showroom photo
 *               (public/presets/autoexperten-standard-showroom.jpg)
 * - "fallback": a generated stand-in because the master photo is missing
 */
export type ShowroomSource = "master" | "fallback";

export const SHOWROOM_SOURCES: readonly ShowroomSource[] = ["master", "fallback"];

/** Response header of GET /showroom/{preset}.jpg naming the showroom source. */
export const SHOWROOM_SOURCE_HEADER = "X-Showroom-Source";

export interface ProcessorWarning {
  code: string;
  /** German, user-presentable. */
  message: string;
}

export interface ProcessorJobMetadata {
  shotKind: string;
  segmenter: string;
  model: string;
  /** True while the processor composites onto the generated fallback showroom. */
  showroomPlaceholder: boolean;
  /** Showroom used for this job; null if the processor does not report it. */
  showroomSource: ShowroomSource | null;
  /** Debug file names (only with PROCESSOR_DEBUG=true), e.g. "mask.png". */
  debugFiles: string[];
  timingsMs: Record<string, number>;
  placement?: Record<string, unknown>;
  adjustments?: Record<string, unknown>;
}

export interface ProcessorJob {
  jobId: string;
  vehicleId: string | null;
  photoId: string | null;
  preset: string;
  status: ProcessingJobStatus;
  /** 0..1 */
  progress: number;
  createdAt: string;
  updatedAt: string;
  result: ProcessorJobResult | null;
  /** German, user-presentable error message. */
  error: string | null;
  warnings: ProcessorWarning[];
  metadata: ProcessorJobMetadata;
}

export interface ProcessorHealth {
  status: "ok";
  version: string;
  segmenter: string;
  model: string;
  debug: boolean;
  showroomPlaceholder: boolean;
  /** Showroom the processor currently uses; null if it does not report it. */
  showroomSource: ShowroomSource | null;
}

/* ------------------------------------------------------------------------ */
/* Limits & validation shared by the proxy routes and the test page          */
/* ------------------------------------------------------------------------ */

/** Presets the processor implements (the test page offers only these). */
export const DEV_TEST_PRESETS = [
  { id: "autoexperten_standard", name: "AutoExperten Standard" },
] as const;

export type DevTestPresetId = (typeof DEV_TEST_PRESETS)[number]["id"];

export const DEFAULT_DEV_TEST_PRESET: DevTestPresetId = "autoexperten_standard";
export const DEFAULT_DEV_TEST_SHOT_KEY = "front_left_45";

export const DEV_TEST_MAX_UPLOAD_MB = 40;
export const DEV_TEST_MAX_UPLOAD_BYTES = DEV_TEST_MAX_UPLOAD_MB * 1024 * 1024;

/** Width of the showroom preview (GET /showroom/{preset}.jpg?width=N), in px. */
export const DEV_SHOWROOM_WIDTH = { default: 1600, min: 320, max: 3840 } as const;

export const DEV_JOB_ID_PATTERN = /^[A-Za-z0-9_-]{1,128}$/;
export const DEV_DEBUG_NAME_PATTERN = /^[a-z0-9_.-]{1,64}$/;

export function isDevTestPresetId(value: unknown): value is DevTestPresetId {
  return DEV_TEST_PRESETS.some((preset) => preset.id === value);
}

export function isValidDevJobId(value: unknown): value is string {
  return typeof value === "string" && DEV_JOB_ID_PATTERN.test(value);
}

/** Strict file name check – never lets "." / ".." or path segments through. */
export function isValidDebugFileName(value: unknown): value is string {
  return (
    typeof value === "string" &&
    DEV_DEBUG_NAME_PATTERN.test(value) &&
    !value.startsWith(".") &&
    !value.includes("..")
  );
}

export function isShowroomSource(value: unknown): value is ShowroomSource {
  return SHOWROOM_SOURCES.some((source) => source === value);
}

/** Strict showroom source parsing – anything unknown becomes null. */
export function parseShowroomSource(value: unknown): ShowroomSource | null {
  return isShowroomSource(value) ? value : null;
}

/**
 * `width` query parameter of the showroom proxy: absent → default, otherwise
 * a plain integer within DEV_SHOWROOM_WIDTH. Returns null if invalid.
 */
export function parseShowroomWidth(value: string | null): number | null {
  if (value === null) return DEV_SHOWROOM_WIDTH.default;
  if (!/^[0-9]{1,5}$/.test(value)) return null;
  const width = Number(value);
  return width >= DEV_SHOWROOM_WIDTH.min && width <= DEV_SHOWROOM_WIDTH.max ? width : null;
}

/** A width for the showroom preview matching `preferred` (e.g. the result width) if allowed. */
export function showroomPreviewWidth(preferred: number | null | undefined): number {
  if (typeof preferred !== "number" || !Number.isInteger(preferred) || preferred <= 0) {
    return DEV_SHOWROOM_WIDTH.default; // unknown (the processor reports 0)
  }
  return Math.min(DEV_SHOWROOM_WIDTH.max, Math.max(DEV_SHOWROOM_WIDTH.min, preferred));
}

/** Same-origin proxy URLs used by the browser. */
export const DEV_TEST_API = {
  base: "/api/dev/processing-test",
  job: (jobId: string) => `/api/dev/processing-test/${encodeURIComponent(jobId)}`,
  result: (jobId: string, download = false) =>
    `/api/dev/processing-test/${encodeURIComponent(jobId)}/result${download ? "?download=1" : ""}`,
  debug: (jobId: string, name: string) =>
    `/api/dev/processing-test/${encodeURIComponent(jobId)}/debug/${encodeURIComponent(name)}`,
  showroom: (preset: DevTestPresetId, width: number = DEV_SHOWROOM_WIDTH.default) =>
    `/api/dev/processing-test/showroom?${new URLSearchParams({ preset, width: String(width) })}`,
} as const;

/* ------------------------------------------------------------------------ */
/* Defensive parsing of processor responses                                  */
/* ------------------------------------------------------------------------ */

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function parseResult(value: unknown): ProcessorJobResult | null {
  const result = asRecord(value);
  if (!result) return null;
  if (result.kind === "stored" && typeof result.processedStoragePath === "string") {
    return { kind: "stored", processedStoragePath: result.processedStoragePath };
  }
  if (result.kind === "file") {
    return {
      kind: "file",
      resultUrl: asString(result.resultUrl),
      width: asNumber(result.width),
      height: asNumber(result.height),
      bytes: asNumber(result.bytes),
    };
  }
  return null;
}

function parseWarnings(value: unknown): ProcessorWarning[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const warning = asRecord(item);
    if (!warning || typeof warning.message !== "string") return [];
    return [{ code: asString(warning.code, "warning"), message: warning.message }];
  });
}

/**
 * Showroom status of a job or health response. `showroomPlaceholder: true`
 * (older processors) always means the fallback showroom – when in doubt the
 * page warns rather than claiming the master photo was used.
 */
function parseShowroomStatus(record: Record<string, unknown>): {
  showroomPlaceholder: boolean;
  showroomSource: ShowroomSource | null;
} {
  const source =
    record.showroomPlaceholder === true ? "fallback" : parseShowroomSource(record.showroomSource);
  return { showroomPlaceholder: source === "fallback", showroomSource: source };
}

function parseTimings(value: unknown): Record<string, number> {
  const timings = asRecord(value);
  if (!timings) return {};
  return Object.fromEntries(
    Object.entries(timings).filter(
      (entry): entry is [string, number] => typeof entry[1] === "number" && Number.isFinite(entry[1]),
    ),
  );
}

function parseMetadata(value: unknown): ProcessorJobMetadata {
  const metadata = asRecord(value) ?? {};
  const placement = asRecord(metadata.placement);
  const adjustments = asRecord(metadata.adjustments);
  return {
    shotKind: asString(metadata.shotKind),
    segmenter: asString(metadata.segmenter),
    model: asString(metadata.model),
    ...parseShowroomStatus(metadata),
    debugFiles: Array.isArray(metadata.debugFiles)
      ? metadata.debugFiles.filter(isValidDebugFileName)
      : [],
    timingsMs: parseTimings(metadata.timingsMs),
    ...(placement ? { placement } : {}),
    ...(adjustments ? { adjustments } : {}),
  };
}

/**
 * Validates the essential fields of a processor job response and fills
 * defaults for optional ones. Returns null if the response is unusable.
 */
export function parseProcessorJob(value: unknown): ProcessorJob | null {
  const job = asRecord(value);
  if (!job || !isValidDevJobId(job.jobId)) return null;
  const status = job.status;
  if (!PROCESSING_JOB_STATUSES.includes(status as ProcessingJobStatus)) return null;
  return {
    jobId: job.jobId,
    vehicleId: typeof job.vehicleId === "string" ? job.vehicleId : null,
    photoId: typeof job.photoId === "string" ? job.photoId : null,
    preset: asString(job.preset, DEFAULT_DEV_TEST_PRESET),
    status: status as ProcessingJobStatus,
    progress: Math.min(1, Math.max(0, asNumber(job.progress))),
    createdAt: asString(job.createdAt),
    updatedAt: asString(job.updatedAt),
    result: parseResult(job.result),
    error: typeof job.error === "string" && job.error.length > 0 ? job.error : null,
    warnings: parseWarnings(job.warnings),
    metadata: parseMetadata(job.metadata),
  };
}

export function parseProcessorHealth(value: unknown): ProcessorHealth | null {
  const health = asRecord(value);
  if (!health || health.status !== "ok") return null;
  return {
    status: "ok",
    version: asString(health.version),
    segmenter: asString(health.segmenter),
    model: asString(health.model),
    debug: health.debug === true,
    ...parseShowroomStatus(health),
  };
}
