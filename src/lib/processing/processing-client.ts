/**
 * Browser client for the app's processing API. The UI only uses these
 * functions – it never talks to the processor directly and never sees its
 * URL, key or result URLs.
 */
import { AppError, type AppErrorCode } from "@/lib/errors";
import type { ProcessingPresetId } from "@/lib/domain/types";
import {
  ACCESS_CODE_REQUIRED_CODE,
  PROCESSING_ACCESS_CODE_HEADER,
  PROCESSING_BUSY_CODE,
  PROCESSING_NOT_CONNECTED_CODE,
  PROCESSING_NOT_CONNECTED_MESSAGE,
  isTerminalJobStatus,
  type ApiErrorBody,
  type ProcessPhotoRequest,
  type ProcessPhotoResponse,
  type ProcessingJob,
  type ProcessingStatus,
} from "./types";

/** A non-OK answer of the app's processing API (`status`: its HTTP status). */
class ProcessingApiError extends AppError {
  constructor(
    readonly status: number,
    code: AppErrorCode,
    options: { userMessage?: string } = {},
  ) {
    super(code, options);
  }
}

/** The processor is busy (queue full) – worth retrying after a short wait. */
class ProcessingBusyError extends ProcessingApiError {}

/** Demo mode: PROCESSING_ACCESS_CODE is set and the code is missing or wrong. */
export class AccessCodeRequiredError extends ProcessingApiError {}

/** No real processor is configured or reachable. */
export class ProcessingNotConnectedError extends ProcessingApiError {}

/** 409: the result file is not available yet. */
class ResultNotReadyError extends ProcessingApiError {}

/**
 * A full processor queue (503 processing_busy) is retried until this long
 * after the first attempt – with several employees processing at once a
 * photo may have to wait minutes for a free slot.
 */
const BUSY_RETRY_DEADLINE_MS = 10 * 60_000;
/**
 * Busy back-off: the wait doubles from the first to the longest one, with
 * "equal jitter" (busyRetryWait: half to all of it). Demo uploads send the
 * photo copy (up to 4 MB) again with every attempt, so in a long busy phase a
 * photo is re-sent every 30–60 s.
 */
const BUSY_RETRY_FIRST_WAIT_MS = 3_000;
const BUSY_RETRY_MAX_WAIT_MS = 60_000;
/** Waits when the result file is not ready yet although the job reported "complete". */
const RESULT_RETRY_DELAYS_MS = [700, 1_500, 3_000];
/** Waits after failed requests (the job keeps running on the processor); the last one repeats. */
const POLL_ERROR_DELAYS_MS = [2_000, 4_000, 8_000, 15_000];
/** A lost connection while downloading a finished result is retried this long (the result stays). */
const RESULT_ERROR_RETRY_MS = 60_000;
const SLOW_JOB_MESSAGE = "Die Bearbeitung dauert ungewöhnlich lange. Bitte später erneut versuchen.";

export interface ProcessingRequestOptions {
  /** Demo mode: value of PROCESSING_ACCESS_CODE entered on this device. */
  accessCode?: string | null;
  signal?: AbortSignal;
}

async function readError(response: Response): Promise<AppError> {
  let message: string | undefined;
  let code: string | undefined;
  try {
    const body = (await response.json()) as Partial<ApiErrorBody>;
    message = body.error?.message;
    code = body.error?.code;
  } catch {
    // ignore
  }
  const { status } = response;
  if (status === 503 && code === PROCESSING_BUSY_CODE) {
    return new ProcessingBusyError(status, "unknown", { userMessage: message });
  }
  if (status === 503 && code === PROCESSING_NOT_CONNECTED_CODE) {
    return new ProcessingNotConnectedError(status, "unknown", {
      userMessage: message ?? PROCESSING_NOT_CONNECTED_MESSAGE,
    });
  }
  if (status === 401 && code === ACCESS_CODE_REQUIRED_CODE) {
    return new AccessCodeRequiredError(status, "unauthorized", { userMessage: message });
  }
  if (status === 409 && code === "not_ready") {
    return new ResultNotReadyError(status, "unknown", { userMessage: message });
  }
  if (status === 401) return new ProcessingApiError(status, "unauthorized", { userMessage: message });
  if (status === 404) return new ProcessingApiError(status, "not_found", { userMessage: message });
  return new ProcessingApiError(status, "unknown", { userMessage: message });
}

/**
 * A failed GET that is worth repeating (status poll, result download): no
 * connection or a server / gateway error (5xx). 4xx answers (401 session or
 * access code, 404 unknown job) and "no processor configured" are final.
 */
function isTransientError(error: unknown): boolean {
  if (error instanceof ProcessingNotConnectedError) return false;
  if (error instanceof ProcessingApiError) return error.status >= 500;
  return error instanceof AppError && error.code === "network";
}

function buildHeaders(options: ProcessingRequestOptions, extra: Record<string, string> = {}): Headers {
  const headers = new Headers(extra);
  const code = options.accessCode?.trim();
  // URI-encoded: header values must be Latin-1, codes may contain any character.
  if (code) headers.set(PROCESSING_ACCESS_CODE_HEADER, encodeURIComponent(code));
  return headers;
}

async function send(input: string, init: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(input, { ...init, cache: "no-store" });
  } catch (error) {
    if (init.signal?.aborted) throw error;
    throw new AppError("network", { cause: error });
  }
  if (!response.ok) throw await readError(response);
  return response;
}

async function requestJson<T>(input: string, init: RequestInit): Promise<T> {
  const response = await send(input, init);
  try {
    return (await response.json()) as T;
  } catch (error) {
    throw new AppError("unknown", { cause: error });
  }
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

/**
 * Exponential back-off with "equal jitter" (half fixed, half random), so
 * devices waiting for the same processor do not retry in lockstep.
 */
function busyRetryWait(attempt: number): number {
  const wait = Math.min(BUSY_RETRY_MAX_WAIT_MS, BUSY_RETRY_FIRST_WAIT_MS * 2 ** attempt);
  return wait / 2 + Math.random() * (wait / 2);
}

/** Retries `call` while the processor queue is full (503 processing_busy), up to BUSY_RETRY_DEADLINE_MS. */
async function withBusyRetry<T>(call: () => Promise<T>, signal?: AbortSignal): Promise<T> {
  const deadline = Date.now() + BUSY_RETRY_DEADLINE_MS;
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await call();
    } catch (error) {
      if (!(error instanceof ProcessingBusyError)) throw error;
      const wait = busyRetryWait(attempt);
      if (Date.now() + wait > deadline) throw error;
      await delay(wait, signal);
    }
  }
}

/** GET /api/processing-status */
export function fetchProcessingStatus(signal?: AbortSignal): Promise<ProcessingStatus> {
  return requestJson<ProcessingStatus>("/api/processing-status", { signal });
}

/** Supabase mode: POST /api/process-photo – the processor reads the original from storage. */
export function submitPhotoProcessing(
  body: ProcessPhotoRequest,
  options: ProcessingRequestOptions = {},
): Promise<ProcessPhotoResponse> {
  return withBusyRetry(
    () =>
      requestJson<ProcessPhotoResponse>("/api/process-photo", {
        method: "POST",
        headers: buildHeaders(options, { "Content-Type": "application/json" }),
        body: JSON.stringify(body),
        signal: options.signal,
      }),
    options.signal,
  );
}

/** Demo mode: POST /api/process-upload (multipart file, preset, shotKey). */
export function submitUploadProcessing(
  input: { file: Blob; preset: ProcessingPresetId; shotKey: string },
  options: ProcessingRequestOptions = {},
): Promise<ProcessPhotoResponse> {
  return withBusyRetry(() => {
    // A fresh body per attempt.
    const form = new FormData();
    form.append("file", input.file, `${input.shotKey}.jpg`);
    form.append("preset", input.preset);
    form.append("shotKey", input.shotKey);
    return requestJson<ProcessPhotoResponse>("/api/process-upload", {
      method: "POST",
      headers: buildHeaders(options),
      body: form,
      signal: options.signal,
    });
  }, options.signal);
}

/** GET /api/process-job/:jobId */
export function fetchProcessingJob(jobId: string, options: ProcessingRequestOptions = {}): Promise<ProcessingJob> {
  return requestJson<ProcessingJob>(`/api/process-job/${encodeURIComponent(jobId)}`, {
    headers: buildHeaders(options),
    signal: options.signal,
  });
}

/**
 * Polls a job until it is complete or failed.
 *
 * `timeoutMs` counts from when the processor starts working on the job;
 * while the job still waits in the processor's queue (other photos first)
 * the generous `queuedTimeoutMs` applies. A failed poll (no connection, 5xx)
 * does not stop the job on the processor: it is repeated with back-off as
 * long as the last answer is at most `timeoutMs` old.
 */
export async function waitForProcessingJob(
  jobId: string,
  options: ProcessingRequestOptions & {
    intervalMs?: number;
    timeoutMs?: number;
    queuedTimeoutMs?: number;
    onUpdate?: (job: ProcessingJob) => void;
  } = {},
): Promise<ProcessingJob> {
  const { intervalMs = 1_000, timeoutMs = 5 * 60_000, queuedTimeoutMs = 30 * 60_000, signal, onUpdate } = options;
  let deadline = Date.now() + queuedTimeoutMs;
  let processing = false;
  let lastAnswerAt = Date.now();
  let failedPolls = 0;
  for (;;) {
    try {
      const job = await fetchProcessingJob(jobId, options);
      lastAnswerAt = Date.now();
      failedPolls = 0;
      onUpdate?.(job);
      if (isTerminalJobStatus(job.status)) return job;
      if (job.status !== "queued" && !processing) {
        processing = true;
        deadline = Date.now() + timeoutMs;
      }
    } catch (error) {
      if (signal?.aborted || !isTransientError(error)) throw error;
      if (Date.now() - lastAnswerAt > timeoutMs || Date.now() > deadline) throw error;
      failedPolls += 1;
    }
    if (Date.now() > deadline) throw new AppError("unknown", { userMessage: SLOW_JOB_MESSAGE });
    const backoff = POLL_ERROR_DELAYS_MS[Math.min(failedPolls, POLL_ERROR_DELAYS_MS.length) - 1];
    await delay(failedPolls === 0 ? intervalMs : (backoff ?? intervalMs), signal);
  }
}

/** The body of an answer; a connection lost while it streams is a network error (worth retrying). */
async function readBlob(response: Response, signal?: AbortSignal): Promise<Blob> {
  try {
    return await response.blob();
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new AppError("network", { cause: error });
  }
}

/**
 * Upload jobs: downloads the processed JPEG via GET /api/process-job/:jobId/result
 * (the app's own route – never the processor's URL). A result that is not
 * ready yet (409) and a lost connection (also while the file streams) or
 * gateway error are retried; the finished result stays on the processor
 * meanwhile.
 */
export async function downloadProcessingResult(
  jobId: string,
  options: ProcessingRequestOptions = {},
): Promise<Blob> {
  const startedAt = Date.now();
  let notReady = 0;
  let failedAttempts = 0;
  for (;;) {
    let type: string | undefined;
    let blob: Blob;
    try {
      const response = await send(`/api/process-job/${encodeURIComponent(jobId)}/result`, {
        headers: buildHeaders(options, { Accept: "image/jpeg" }),
        signal: options.signal,
      });
      type = response.headers.get("content-type")?.split(";")[0]?.trim().toLowerCase();
      blob = await readBlob(response, options.signal);
    } catch (error) {
      if (options.signal?.aborted) throw error;
      let wait: number | undefined;
      if (error instanceof ResultNotReadyError) {
        wait = RESULT_RETRY_DELAYS_MS[notReady++];
      } else if (isTransientError(error) && Date.now() - startedAt < RESULT_ERROR_RETRY_MS) {
        wait = POLL_ERROR_DELAYS_MS[Math.min(failedAttempts++, POLL_ERROR_DELAYS_MS.length - 1)];
      }
      if (wait === undefined) throw error;
      await delay(wait, options.signal);
      continue;
    }
    if (type !== "image/jpeg" || blob.size === 0) {
      throw new AppError("unknown", { userMessage: "Das bearbeitete Foto konnte nicht geladen werden." });
    }
    return blob.type === "image/jpeg" ? blob : new Blob([blob], { type: "image/jpeg" });
  }
}
