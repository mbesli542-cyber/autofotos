/**
 * MockImageProcessor – simulates a processing service without doing any
 * image work.
 *
 * It is STATELESS: the job id encodes the request and its creation time, and
 * the status is derived from the elapsed time. This works on serverless
 * hosting where consecutive requests may hit different instances.
 */
import { isProcessingPresetId, type ProcessingPresetId } from "@/lib/domain/types";
import type {
  ImageProcessor,
  ProcessPhotoRequest,
  ProcessingJob,
  ProcessingJobStatus,
} from "./types";

export interface MockProcessorTiming {
  queuedMs: number;
  processingMs: number;
}

export const DEFAULT_MOCK_TIMING: MockProcessorTiming = {
  queuedMs: 600,
  processingMs: 2200,
};

const JOB_PREFIX = "mock_";

/** Compact job payload encoded into the job id. */
interface EncodedJob {
  v: string;
  p: string;
  s: ProcessingPresetId;
  t: number;
}

function toBase64Url(value: string): string {
  return Buffer.from(value, "utf8").toString("base64url");
}

function fromBase64Url(value: string): string {
  return Buffer.from(value, "base64url").toString("utf8");
}

export class MockImageProcessor implements ImageProcessor {
  readonly name = "mock";

  constructor(
    private readonly timing: MockProcessorTiming = DEFAULT_MOCK_TIMING,
    private readonly now: () => number = Date.now,
  ) {}

  async submit(request: ProcessPhotoRequest): Promise<ProcessingJob> {
    const payload: EncodedJob = {
      v: request.vehicleId,
      p: request.photoId,
      s: request.preset,
      t: this.now(),
    };
    const jobId = `${JOB_PREFIX}${toBase64Url(JSON.stringify(payload))}`;
    return this.buildJob(jobId, payload);
  }

  async getJob(jobId: string): Promise<ProcessingJob | null> {
    const payload = this.decode(jobId);
    return payload ? this.buildJob(jobId, payload) : null;
  }

  private decode(jobId: string): EncodedJob | null {
    if (!jobId.startsWith(JOB_PREFIX)) return null;
    try {
      const parsed: unknown = JSON.parse(fromBase64Url(jobId.slice(JOB_PREFIX.length)));
      if (typeof parsed !== "object" || parsed === null) return null;
      const { v, p, s, t } = parsed as Record<string, unknown>;
      if (typeof v !== "string" || typeof p !== "string" || typeof t !== "number") {
        return null;
      }
      if (!isProcessingPresetId(s)) return null;
      return { v, p, s, t };
    } catch {
      return null;
    }
  }

  private buildJob(jobId: string, payload: EncodedJob): ProcessingJob {
    const elapsed = Math.max(0, this.now() - payload.t);
    const { queuedMs, processingMs } = this.timing;

    let status: ProcessingJobStatus;
    let progress: number;
    if (elapsed < queuedMs) {
      status = "queued";
      progress = 0;
    } else if (elapsed < queuedMs + processingMs) {
      status = "processing";
      progress = Math.min(0.99, (elapsed - queuedMs) / processingMs);
    } else {
      status = "complete";
      progress = 1;
    }

    const createdAt = new Date(payload.t).toISOString();
    const updatedAt = new Date(
      payload.t + Math.min(elapsed, queuedMs + processingMs),
    ).toISOString();

    return {
      jobId,
      vehicleId: payload.v,
      photoId: payload.p,
      preset: payload.s,
      status,
      progress,
      createdAt,
      updatedAt,
      result: status === "complete" ? { kind: "mock_preview" } : null,
      error: null,
    };
  }
}
