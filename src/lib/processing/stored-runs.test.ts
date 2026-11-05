import { describe, expect, it } from "vitest";
import type { JournalStorage } from "./job-journal";
import { STORED_RUNS_KEY, forgetRun, parseStoredRuns, readStoredRun, storeRun, type StoredRun } from "./stored-runs";

const NOW = Date.parse("2026-11-05T10:00:00.000Z");

function run(patch: Partial<StoredRun> = {}): StoredRun {
  return {
    vehicleId: "veh_1",
    preset: "autoexperten_standard",
    reprocess: false,
    startedAt: new Date(NOW - 60_000).toISOString(),
    items: [
      {
        photoId: "p_front",
        shotKey: "front",
        title: "Vorne",
        shotOrder: 2,
        treatment: "showroom",
        status: "complete",
        error: null,
        errorCode: null,
        warnings: [{ code: "low_resolution", message: "Vergrößert." }],
      },
      {
        photoId: "p_rear",
        shotKey: "rear",
        title: "Hinten",
        shotOrder: 7,
        treatment: "showroom",
        status: "open",
        error: null,
        errorCode: null,
        warnings: [],
      },
    ],
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

describe("stored runs", () => {
  it("keeps one run per vehicle and forgets it", () => {
    const storage = memoryStorage();
    storeRun(run(), storage, NOW);
    storeRun(run({ reprocess: true }), storage, NOW);
    storeRun(run({ vehicleId: "veh_2" }), storage, NOW);
    expect(readStoredRun("veh_1", storage, NOW)).toEqual(run({ reprocess: true }));
    forgetRun("veh_1", storage, NOW);
    expect(readStoredRun("veh_1", storage, NOW)).toBeNull();
    forgetRun("veh_2", storage, NOW);
    expect(storage.data.has(STORED_RUNS_KEY)).toBe(false);
  });

  it("ignores runs older than the processor keeps its jobs and malformed runs", () => {
    const raw = JSON.stringify([
      run({ vehicleId: "veh_ok" }),
      run({ vehicleId: "veh_old", startedAt: new Date(NOW - 25 * 3_600_000).toISOString() }),
      run({ vehicleId: "veh_empty", items: [] }),
      { ...run({ vehicleId: "veh_bad_item" }), items: [{ photoId: "p" }] },
      { ...run({ vehicleId: "veh_bad_preset" }), preset: "flux" },
      42,
    ]);
    expect(parseStoredRuns(raw, NOW).map((entry) => entry.vehicleId)).toEqual(["veh_ok"]);
    expect(parseStoredRuns("[", NOW)).toEqual([]);
    expect(parseStoredRuns(null, NOW)).toEqual([]);
  });

  it("works with a storage that throws", () => {
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
    expect(() => storeRun(run(), throwing, NOW)).not.toThrow();
    expect(readStoredRun("veh_1", throwing, NOW)).toBeNull();
    expect(() => forgetRun("veh_1", throwing, NOW)).not.toThrow();
  });
});
