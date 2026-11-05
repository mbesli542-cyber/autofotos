/**
 * "Fotos bearbeiten" runs kept on this device (localStorage, best effort) so
 * a run survives a reload or a discarded tab: its finished rows are shown as
 * they were and the rows still on their way are processed again – their
 * jobs are polled, not sent again (job-journal.ts, processing-runs.ts). A
 * run older than the processor keeps its jobs (24 h) is ignored; a missing
 * or throwing localStorage only means a reload starts with the style
 * selection again.
 */
import { isProcessingPresetId, type ProcessingPresetId, type ProcessingWarning } from "@/lib/domain/types";
import { JOB_MAX_AGE_MS, type JournalStorage } from "./job-journal";
import { parseProcessingWarnings } from "./processing-warnings";
import type { ShotTreatment } from "./shot-treatment";

export const STORED_RUNS_KEY = "ae-photo.processing-runs.v1";

/** A row of a stored run (as processing-runs.ts shows it). */
export interface StoredRunItem {
  photoId: string;
  shotKey: string;
  title: string;
  shotOrder: number;
  treatment: ShotTreatment;
  /** Finished rows keep their outcome; any other row is processed again after a reload. */
  status: "complete" | "failed" | "open";
  error: string | null;
  errorCode: string | null;
  warnings: readonly ProcessingWarning[];
}

export interface StoredRun {
  vehicleId: string;
  preset: ProcessingPresetId;
  /** Started with "Bereits geprüfte Fotos erneut bearbeiten" (every photo sent again). */
  reprocess: boolean;
  /** When the run started (ISO). */
  startedAt: string;
  items: StoredRunItem[];
}

function browserStorage(): JournalStorage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

const nullableString = (value: unknown): value is string | null => value === null || typeof value === "string";

function parseItem(value: unknown): StoredRunItem | null {
  if (typeof value !== "object" || value === null) return null;
  const item = value as Record<string, unknown>;
  if (
    typeof item.photoId !== "string" ||
    typeof item.shotKey !== "string" ||
    typeof item.title !== "string" ||
    typeof item.shotOrder !== "number" ||
    (item.treatment !== "showroom" && item.treatment !== "original_environment") ||
    (item.status !== "complete" && item.status !== "failed" && item.status !== "open") ||
    !nullableString(item.error) ||
    !nullableString(item.errorCode)
  ) {
    return null;
  }
  return {
    photoId: item.photoId,
    shotKey: item.shotKey,
    title: item.title,
    shotOrder: item.shotOrder,
    treatment: item.treatment,
    status: item.status,
    error: item.error,
    errorCode: item.errorCode,
    warnings: parseProcessingWarnings(item.warnings),
  };
}

function parseRun(value: unknown, now: number): StoredRun | null {
  if (typeof value !== "object" || value === null) return null;
  const run = value as Record<string, unknown>;
  if (
    typeof run.vehicleId !== "string" ||
    !isProcessingPresetId(run.preset) ||
    typeof run.reprocess !== "boolean" ||
    typeof run.startedAt !== "string" ||
    !Array.isArray(run.items)
  ) {
    return null;
  }
  const started = Date.parse(run.startedAt);
  if (!Number.isFinite(started) || now - started >= JOB_MAX_AGE_MS || started - now >= JOB_MAX_AGE_MS) return null;
  const items = run.items.map(parseItem);
  if (items.length === 0 || items.some((item) => item === null)) return null;
  return {
    vehicleId: run.vehicleId,
    preset: run.preset,
    reprocess: run.reprocess,
    startedAt: run.startedAt,
    items: items as StoredRunItem[],
  };
}

/** The valid stored runs the processor may still have jobs of (pure). */
export function parseStoredRuns(raw: string | null, now: number): StoredRun[] {
  if (!raw) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  return parsed.flatMap((value) => {
    const run = parseRun(value, now);
    return run ? [run] : [];
  });
}

function readAll(storage: JournalStorage | null, now: number): StoredRun[] {
  try {
    return parseStoredRuns(storage?.getItem(STORED_RUNS_KEY) ?? null, now);
  } catch {
    return [];
  }
}

function writeAll(storage: JournalStorage | null, runs: readonly StoredRun[]): void {
  try {
    if (runs.length > 0) storage?.setItem(STORED_RUNS_KEY, JSON.stringify(runs));
    else storage?.removeItem(STORED_RUNS_KEY);
  } catch {
    // Storage blocked or full: the run just cannot be resumed after a reload.
  }
}

/** The stored run of the vehicle, if any. */
export function readStoredRun(vehicleId: string, storage = browserStorage(), now = Date.now()): StoredRun | null {
  return readAll(storage, now).find((run) => run.vehicleId === vehicleId) ?? null;
}

/** Keeps the vehicle's run (replaces the one stored before). */
export function storeRun(run: StoredRun, storage = browserStorage(), now = Date.now()): void {
  writeAll(storage, [...readAll(storage, now).filter((stored) => stored.vehicleId !== run.vehicleId), run]);
}

/** Forgets the vehicle's run (finished, reset, signed out). */
export function forgetRun(vehicleId: string, storage = browserStorage(), now = Date.now()): void {
  const runs = readAll(storage, now);
  const kept = runs.filter((run) => run.vehicleId !== vehicleId);
  if (kept.length !== runs.length) writeAll(storage, kept);
}
