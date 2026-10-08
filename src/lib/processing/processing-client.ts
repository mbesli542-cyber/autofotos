/**
 * Browser client for the app's processing API. The UI only uses these
 * functions – it never talks to the processor directly and never sees its
 * URL, key or result URLs.
 */
import { AppError } from "@/lib/errors";
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

/** The processor is busy (queue full) – worth retrying after a short wait. */
class ProcessingBusyError extends AppError {}

/** Demo mode: PROCESSING_ACCESS_CODE is set and the code is missing or wrong. */
export class AccessCodeRequiredError extends AppError {}

/** No real processor is configured or reachable. */
export class ProcessingNotConnectedError extends AppError {}

/** 409: the result file is not available yet. */
class ResultNotReadyError extends AppError {}

/** Waits between attempts when the processor is busy (≈ 1 min in total). */
const BUSY_RETRY_DELAYS_MS = [3_000, 8_000, 15_000, 30_000];
/** Waits when the result file is not ready yet although the job reported "complete". */
const RESULT_RETRY_DELAYS_MS = [700, 1_500, 3_000];

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
  if (response.status === 503 && code === PROCESSING_BUSY_CODE) {
    return new ProcessingBusyError("unknown", { userMessage: message });
  }
  if (response.status === 503 && code === PROCESSING_NOT_CONNECTED_CODE) {
    return new ProcessingNotConnectedError("unknown", { userMessage: message ?? PROCESSING_NOT_CONNECTED_MESSAGE });
  }
  if (response.status === 401 && code === ACCESS_CODE_REQUIRED_CODE) {
    return new AccessCodeRequiredError("unauthorized", { userMessage: message });
  }
  if (response.status === 409 && code === "not_ready") {
    return new ResultNotReadyError("unknown", { userMessage: message });
  }
  if (response.status === 401) return new AppError("unauthorized", { userMessage: message });
  if (response.status === 404) return new AppError("not_found", { userMessage: message });
  return new AppError("unknown", { userMessage: message });
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

/** Retries `call` while the processor queue is full (503 processing_busy). */
async function withBusyRetry<T>(call: () => Promise<T>, signal?: AbortSignal): Promise<T> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await call();
    } catch (error) {
      const wait = BUSY_RETRY_DELAYS_MS[attempt];
      if (!(error instanceof ProcessingBusyError) || wait === undefined) throw error;
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

/** Polls a job until it is complete or failed. */
export async function waitForProcessingJob(
  jobId: string,
  options: ProcessingRequestOptions & {
    intervalMs?: number;
    timeoutMs?: number;
    onUpdate?: (job: ProcessingJob) => void;
  } = {},
): Promise<ProcessingJob> {
  const { intervalMs = 1_000, timeoutMs = 5 * 60_000, signal, onUpdate } = options;
  const startedAt = Date.now();
  for (;;) {
    const job = await fetchProcessingJob(jobId, options);
    onUpdate?.(job);
    if (isTerminalJobStatus(job.status)) return job;
    if (Date.now() - startedAt > timeoutMs) {
      throw new AppError("unknown", {
        userMessage: "Die Bearbeitung dauert ungewöhnlich lange. Bitte später erneut versuchen.",
      });
    }
    await delay(intervalMs, signal);
  }
}

/**
 * Upload jobs: downloads the processed JPEG via GET /api/process-job/:jobId/result
 * (the app's own route – never the processor's URL).
 */
export async function downloadProcessingResult(
  jobId: string,
  options: ProcessingRequestOptions = {},
): Promise<Blob> {
  for (let attempt = 0; ; attempt += 1) {
    let response: Response;
    try {
      response = await send(`/api/process-job/${encodeURIComponent(jobId)}/result`, {
        headers: buildHeaders(options, { Accept: "image/jpeg" }),
        signal: options.signal,
      });
    } catch (error) {
      const wait = RESULT_RETRY_DELAYS_MS[attempt];
      if (!(error instanceof ResultNotReadyError) || wait === undefined) throw error;
      await delay(wait, options.signal);
      continue;
    }
    const type = response.headers.get("content-type")?.split(";")[0]?.trim().toLowerCase();
    const blob = await response.blob();
    if (type !== "image/jpeg" || blob.size === 0) {
      throw new AppError("unknown", { userMessage: "Das bearbeitete Foto konnte nicht geladen werden." });
    }
    return blob.type === "image/jpeg" ? blob : new Blob([blob], { type: "image/jpeg" });
  }
}
