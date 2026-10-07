/**
 * Browser client for the processing API. The UI only uses these functions –
 * it never knows whether a mock or the real processor is behind the API.
 */
import { AppError } from "@/lib/errors";
import {
  isTerminalJobStatus,
  type ApiErrorBody,
  type ProcessPhotoRequest,
  type ProcessPhotoResponse,
  type ProcessingJob,
} from "./types";

async function readError(response: Response): Promise<AppError> {
  let message: string | undefined;
  try {
    const body = (await response.json()) as Partial<ApiErrorBody>;
    message = body.error?.message;
  } catch {
    // ignore
  }
  if (response.status === 401) return new AppError("unauthorized", { userMessage: message });
  if (response.status === 404) return new AppError("not_found", { userMessage: message });
  return new AppError("unknown", { userMessage: message });
}

async function request<T>(input: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(input, { ...init, cache: "no-store" });
  } catch (error) {
    throw new AppError("network", { cause: error });
  }
  if (!response.ok) throw await readError(response);
  return (await response.json()) as T;
}

export function submitPhotoProcessing(body: ProcessPhotoRequest): Promise<ProcessPhotoResponse> {
  return request<ProcessPhotoResponse>("/api/process-photo", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function fetchProcessingJob(jobId: string): Promise<ProcessingJob> {
  return request<ProcessingJob>(`/api/process-job/${encodeURIComponent(jobId)}`);
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
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

/** Polls a job until it is complete or failed. */
export async function waitForProcessingJob(
  jobId: string,
  options: {
    intervalMs?: number;
    timeoutMs?: number;
    signal?: AbortSignal;
    onUpdate?: (job: ProcessingJob) => void;
  } = {},
): Promise<ProcessingJob> {
  const { intervalMs = 700, timeoutMs = 5 * 60_000, signal, onUpdate } = options;
  const startedAt = Date.now();
  for (;;) {
    const job = await fetchProcessingJob(jobId);
    onUpdate?.(job);
    if (isTerminalJobStatus(job.status)) return job;
    if (Date.now() - startedAt > timeoutMs) {
      throw new AppError("unknown", {
        userMessage: "Die Bearbeitung dauert ungewöhnlich lange. Bitte später erneut prüfen.",
      });
    }
    await delay(intervalMs, signal);
  }
}
