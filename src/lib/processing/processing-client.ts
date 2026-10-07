/**
 * Browser client for the processing API. The UI only uses these functions –
 * it never knows whether a mock or the real processor is behind the API.
 */
import { AppError } from "@/lib/errors";
import {
  PROCESSING_BUSY_CODE,
  isTerminalJobStatus,
  type ApiErrorBody,
  type ProcessPhotoRequest,
  type ProcessPhotoResponse,
  type ProcessingJob,
} from "./types";

/** The processor is busy (queue full) – worth retrying after a short wait. */
class ProcessingBusyError extends AppError {}

/** Waits between attempts when the processor is busy (≈ 1 min in total). */
const BUSY_RETRY_DELAYS_MS = [3_000, 8_000, 15_000, 30_000];

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

export async function submitPhotoProcessing(body: ProcessPhotoRequest): Promise<ProcessPhotoResponse> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await request<ProcessPhotoResponse>("/api/process-photo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (error) {
      const wait = BUSY_RETRY_DELAYS_MS[attempt];
      if (!(error instanceof ProcessingBusyError) || wait === undefined) throw error;
      await delay(wait);
    }
  }
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
