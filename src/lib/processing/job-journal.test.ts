import { describe, expect, it } from "vitest";
import {
  JOB_JOURNAL_KEY,
  JOB_MAX_AGE_MS,
  clearJobJournal,
  findJob,
  forgetJob,
  parseJobJournal,
  readJobJournal,
  recordJob,
  type JournalEntry,
  type JournalStorage,
} from "./job-journal";

const NOW = Date.parse("2026-11-05T10:00:00.000Z");

function entry(patch: Partial<JournalEntry> = {}): JournalEntry {
  return {
    userId: "u_anna",
    vehicleId: "veh_1",
    photoId: "p_front",
    jobId: "0123456789abcdef0123456789abcdef",
    preset: "autoexperten_standard",
    startedAt: new Date(NOW - 60_000).toISOString(),
    shotKey: "front",
    shotOrder: 2,
    title: "Vorne",
    takenAt: "2026-11-05T09:58:00.000Z",
    ...patch,
  };
}

function memoryStorage(): JournalStorage & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => void data.set(key, value),
    removeItem: (key) => void data.delete(key),
  };
}

const throwing: JournalStorage = {
  getItem: () => {
    throw new DOMException("blocked", "SecurityError");
  },
  setItem: () => {
    throw new DOMException("full", "QuotaExceededError");
  },
  removeItem: () => {
    throw new DOMException("blocked", "SecurityError");
  },
};

describe("job journal", () => {
  it("keeps one job per photo and style and forgets it", () => {
    const storage = memoryStorage();
    recordJob(entry(), storage, NOW);
    recordJob(entry({ jobId: "fedcba9876543210fedcba9876543210" }), storage, NOW);
    recordJob(entry({ photoId: "p_rear", shotKey: "rear" }), storage, NOW);
    expect(readJobJournal(storage, NOW).map((job) => [job.photoId, job.jobId])).toEqual([
      ["p_front", "fedcba9876543210fedcba9876543210"],
      ["p_rear", "0123456789abcdef0123456789abcdef"],
    ]);
    expect(findJob("p_front", "autoexperten_standard", storage, NOW)?.jobId).toBe("fedcba9876543210fedcba9876543210");
    expect(findJob("p_front", "autoexperten_dark", storage, NOW)).toBeNull();
    forgetJob("p_front", undefined, storage, NOW);
    expect(readJobJournal(storage, NOW).map((job) => job.photoId)).toEqual(["p_rear"]);
    clearJobJournal(storage);
    expect(storage.data.has(JOB_JOURNAL_KEY)).toBe(false);
  });

  it("ignores jobs the processor no longer keeps (24 h) and anything malformed", () => {
    const stored = JSON.stringify([
      entry({ photoId: "p_fresh" }),
      entry({ photoId: "p_expired", startedAt: new Date(NOW - JOB_MAX_AGE_MS - 1).toISOString() }),
      entry({ photoId: "p_bad_job", jobId: "../../etc" }),
      entry({ photoId: "p_bad_preset", preset: "flux" as JournalEntry["preset"] }),
      // Kept before entries carried their user: nobody to resume it for.
      { ...entry({ photoId: "p_no_user" }), userId: undefined },
      { photoId: "p_partial" },
      null,
      "x",
    ]);
    expect(parseJobJournal(stored, NOW).map((job) => job.photoId)).toEqual(["p_fresh"]);
    expect(parseJobJournal("{not json", NOW)).toEqual([]);
    expect(parseJobJournal(JSON.stringify({ photoId: "p" }), NOW)).toEqual([]);
    expect(parseJobJournal(null, NOW)).toEqual([]);
  });

  it("works without a storage and with a storage that throws (nothing to resume)", () => {
    expect(() => recordJob(entry(), throwing, NOW)).not.toThrow();
    expect(readJobJournal(throwing, NOW)).toEqual([]);
    expect(() => forgetJob("p_front", undefined, throwing, NOW)).not.toThrow();
    expect(() => clearJobJournal(throwing)).not.toThrow();
    expect(readJobJournal(null, NOW)).toEqual([]);
    expect(() => recordJob(entry(), null, NOW)).not.toThrow();
  });
});
