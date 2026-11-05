/**
 * Processor jobs submitted from this device, kept in localStorage (per
 * device, best effort) so a check or "Fotos bearbeiten" survives a reload or
 * a discarded tab (iPhone Safari discards background tabs often): after the
 * reload the job is polled again instead of sending the photo a second time
 * (photo-checks.ts, processing-runs.ts). The processor keeps jobs for 24 h;
 * older entries are ignored. A missing or throwing localStorage (private
 * mode, blocked storage) only means nothing can be resumed after a reload.
 *
 * Every entry belongs to the user who submitted it: only that user's jobs
 * are resumed (a shared device, a session that expired meanwhile), and
 * signing out forgets them all (photo-checks.ts `stopAll`).
 */
import { isProcessingPresetId, type ProcessingPresetId } from "@/lib/domain/types";
import { isValidProcessingJobId } from "./types";

export const JOB_JOURNAL_KEY = "ae-photo.processing-jobs.v1";

/** How long the processor keeps a job (and its result). */
export const JOB_MAX_AGE_MS = 24 * 60 * 60_000;

/** Entries kept at most (oldest dropped first). */
const MAX_ENTRIES = 100;

export interface JournalEntry {
  /** The signed-in user who submitted the job – only that user's jobs are resumed. */
  userId: string;
  vehicleId: string;
  photoId: string;
  jobId: string;
  preset: ProcessingPresetId;
  /** When the job was submitted (ISO). */
  startedAt: string;
  /** The photo's shot – to show it as being checked right after a reload. */
  shotKey: string;
  shotOrder: number;
  title: string;
  /** Capture time of the photo (ISO). */
  takenAt: string;
}

export type JournalStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function browserStorage(): JournalStorage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function isEntry(value: unknown): value is JournalEntry {
  if (typeof value !== "object" || value === null) return false;
  const entry = value as Record<string, unknown>;
  return (
    typeof entry.userId === "string" &&
    typeof entry.vehicleId === "string" &&
    typeof entry.photoId === "string" &&
    isValidProcessingJobId(entry.jobId) &&
    isProcessingPresetId(entry.preset) &&
    typeof entry.startedAt === "string" &&
    typeof entry.shotKey === "string" &&
    typeof entry.shotOrder === "number" &&
    typeof entry.title === "string" &&
    typeof entry.takenAt === "string"
  );
}

/** The valid entries of a stored journal that the processor may still know (pure). */
export function parseJobJournal(raw: string | null, now: number): JournalEntry[] {
  if (!raw) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  return parsed.filter(isEntry).filter((entry) => {
    const started = Date.parse(entry.startedAt);
    return Number.isFinite(started) && now - started < JOB_MAX_AGE_MS && started - now < JOB_MAX_AGE_MS;
  });
}

const sameJob = (entry: JournalEntry, photoId: string, preset?: ProcessingPresetId) =>
  entry.photoId === photoId && (preset === undefined || entry.preset === preset);

function write(storage: JournalStorage | null, entries: readonly JournalEntry[]): void {
  try {
    if (entries.length > 0) storage?.setItem(JOB_JOURNAL_KEY, JSON.stringify(entries.slice(-MAX_ENTRIES)));
    else storage?.removeItem(JOB_JOURNAL_KEY);
  } catch {
    // Storage blocked or full: the job just cannot be resumed after a reload.
  }
}

export function readJobJournal(storage = browserStorage(), now = Date.now()): JournalEntry[] {
  try {
    return parseJobJournal(storage?.getItem(JOB_JOURNAL_KEY) ?? null, now);
  } catch {
    return [];
  }
}

/** The job of `photoId` with `preset` submitted from this device, if the processor may still know it. */
export function findJob(
  photoId: string,
  preset: ProcessingPresetId,
  storage = browserStorage(),
  now = Date.now(),
): JournalEntry | null {
  return readJobJournal(storage, now).find((entry) => sameJob(entry, photoId, preset)) ?? null;
}

/** Keeps a submitted job (replaces an older job of the same photo and style). */
export function recordJob(entry: JournalEntry, storage = browserStorage(), now = Date.now()): void {
  const others = readJobJournal(storage, now).filter((stored) => !sameJob(stored, entry.photoId, entry.preset));
  write(storage, [...others, entry]);
}

/** Forgets the job of a photo with `preset` (without: with every style). */
export function forgetJob(photoId: string, preset?: ProcessingPresetId, storage = browserStorage(), now = Date.now()): void {
  const entries = readJobJournal(storage, now);
  const kept = entries.filter((entry) => !sameJob(entry, photoId, preset));
  if (kept.length !== entries.length) write(storage, kept);
}

/** Forgets every job (signed out, another user signed in). */
export function clearJobJournal(storage = browserStorage()): void {
  write(storage, []);
}
