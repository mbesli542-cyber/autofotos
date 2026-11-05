import { describe, expect, it, vi } from "vitest";
import { formatStorageSize, persistStorage, storageUsageBytes } from "./persistent-storage";

describe("persistStorage", () => {
  it("asks the browser to keep the data and reports its answer", async () => {
    const persist = vi.fn(async () => true);
    expect(await persistStorage({ persisted: async () => false, persist })).toBe(true);
    expect(persist).toHaveBeenCalledOnce();
    expect(await persistStorage({ persisted: async () => false, persist: async () => false })).toBe(false);
  });

  it("does not ask again when the data is already kept", async () => {
    const persist = vi.fn(async () => true);
    expect(await persistStorage({ persisted: async () => true, persist })).toBe(true);
    expect(persist).not.toHaveBeenCalled();
  });

  it("tolerates browsers without the Storage API or that throw", async () => {
    expect(await persistStorage(undefined)).toBeNull();
    expect(await persistStorage({})).toBeNull();
    expect(
      await persistStorage({
        persist: async () => {
          throw new DOMException("denied", "SecurityError");
        },
      }),
    ).toBeNull();
  });
});

describe("storageUsageBytes / formatStorageSize", () => {
  it("reads the usage when known", async () => {
    expect(await storageUsageBytes({ estimate: async () => ({ usage: 36_100_000, quota: 2e9 }) })).toBe(36_100_000);
    expect(await storageUsageBytes({ estimate: async () => ({}) })).toBeNull();
    expect(await storageUsageBytes({})).toBeNull();
    expect(
      await storageUsageBytes({
        estimate: async () => {
          throw new Error("no");
        },
      }),
    ).toBeNull();
  });

  it("formats like the phone's settings (German)", () => {
    expect(formatStorageSize(36_100_000)).toBe("36 MB");
    expect(formatStorageSize(1_000)).toBe("1 MB");
    expect(formatStorageSize(1_500_000_000)).toBe("1,5 GB");
  });
});
