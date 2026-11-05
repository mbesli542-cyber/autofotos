import { describe, expect, it, vi } from "vitest";
import { ScreenWakeLock, type VisibilitySource, type WakeLockApi, type WakeLockSentinelLike } from "./screen-wake-lock";

function fakeVisibility() {
  const listeners = new Set<() => void>();
  const source = {
    visibilityState: "visible" as DocumentVisibilityState,
    addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
  };
  return {
    source: source as unknown as VisibilitySource,
    listeners,
    set(state: DocumentVisibilityState) {
      source.visibilityState = state;
      for (const listener of [...listeners]) listener();
    },
  };
}

function fakeApi() {
  const sentinels: (WakeLockSentinelLike & { released: boolean })[] = [];
  const api: WakeLockApi = {
    request: vi.fn(async () => {
      const sentinel = {
        released: false,
        release: vi.fn(async () => {
          sentinel.released = true;
        }),
      };
      sentinels.push(sentinel);
      return sentinel;
    }),
  };
  return { api, sentinels };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("ScreenWakeLock", () => {
  it("holds the screen on while started and releases it on stop", async () => {
    const { api, sentinels } = fakeApi();
    const visibility = fakeVisibility();
    const lock = new ScreenWakeLock(api, visibility.source);
    lock.start();
    await flush();
    expect(api.request).toHaveBeenCalledWith("screen");
    expect(lock.held).toBe(true);
    lock.stop();
    expect(sentinels[0]?.release).toHaveBeenCalledTimes(1);
    expect(lock.held).toBe(false);
    expect(visibility.listeners.size).toBe(0);
  });

  it("requests it again when the tab is shown after the browser released it on hide", async () => {
    const { api, sentinels } = fakeApi();
    const visibility = fakeVisibility();
    const lock = new ScreenWakeLock(api, visibility.source);
    lock.start();
    await flush();
    // The browser releases the lock when the tab is hidden.
    (sentinels[0] as { released: boolean }).released = true;
    visibility.set("hidden");
    await flush();
    expect(api.request).toHaveBeenCalledTimes(1);
    visibility.set("visible");
    await flush();
    expect(api.request).toHaveBeenCalledTimes(2);
    expect(lock.held).toBe(true);
    lock.stop();
  });

  it("asks only while the tab is visible and releases a lock granted after stop", async () => {
    const { api, sentinels } = fakeApi();
    const visibility = fakeVisibility();
    visibility.set("hidden");
    const lock = new ScreenWakeLock(api, visibility.source);
    lock.start();
    await flush();
    expect(api.request).not.toHaveBeenCalled();
    visibility.set("visible");
    lock.stop(); // stopped while the request is on its way
    await flush();
    expect(sentinels[0]?.release).toHaveBeenCalledTimes(1);
  });

  it("does nothing without the API and survives a refused request", async () => {
    const visibility = fakeVisibility();
    const none = new ScreenWakeLock(null, visibility.source);
    none.start();
    none.stop();
    const refused = new ScreenWakeLock({ request: vi.fn(async () => Promise.reject(new Error("NotAllowedError"))) }, visibility.source);
    refused.start();
    await flush();
    expect(refused.held).toBe(false);
    refused.stop();
  });
});
