/**
 * Keeps the screen on while something long runs in this tab (e.g. "Fotos
 * bearbeiten", several minutes per vehicle): a locked phone suspends the
 * tab and stops the run. Uses the Screen Wake Lock API where the browser has
 * it (Safari ≥ 16.4, Chrome); without it, or when the browser refuses (low
 * battery, not visible), nothing happens. The browser releases the lock
 * whenever the tab is hidden – it is requested again when the tab is shown
 * while the lock is still wanted, and released by `stop`.
 */

/** The parts of WakeLockSentinel used here. */
export interface WakeLockSentinelLike {
  readonly released: boolean;
  release(): Promise<void>;
}

export interface WakeLockApi {
  request(type: "screen"): Promise<WakeLockSentinelLike>;
}

export type VisibilitySource = Pick<Document, "visibilityState" | "addEventListener" | "removeEventListener">;

export class ScreenWakeLock {
  private wanted = false;
  private sentinel: WakeLockSentinelLike | null = null;
  private requesting = false;

  constructor(
    private readonly api: WakeLockApi | null,
    private readonly visibility: VisibilitySource,
  ) {}

  /** Holds the screen on from now on (also after the tab was hidden and shown again). */
  start(): void {
    if (this.wanted) return;
    this.wanted = true;
    this.visibility.addEventListener("visibilitychange", this.onVisibilityChange);
    void this.acquire();
  }

  /** Lets the screen turn off again. */
  stop(): void {
    if (!this.wanted) return;
    this.wanted = false;
    this.visibility.removeEventListener("visibilitychange", this.onVisibilityChange);
    this.releaseHeld();
  }

  /** Whether the screen is held on right now. */
  get held(): boolean {
    return this.sentinel !== null && !this.sentinel.released;
  }

  private readonly onVisibilityChange = () => {
    // Hidden: the browser released the lock. Shown again: requested again.
    if (this.visibility.visibilityState === "visible") void this.acquire();
  };

  private async acquire(): Promise<void> {
    if (!this.api || !this.wanted || this.requesting || this.held) return;
    if (this.visibility.visibilityState !== "visible") return;
    this.requesting = true;
    try {
      const sentinel = await this.api.request("screen");
      this.sentinel = sentinel;
      // Stopped while the request was on its way.
      if (!this.wanted) this.releaseHeld();
    } catch {
      // Refused (no permission, battery saver, not visible): the run goes on without it.
    } finally {
      this.requesting = false;
    }
  }

  private releaseHeld(): void {
    const sentinel = this.sentinel;
    this.sentinel = null;
    if (sentinel && !sentinel.released) void sentinel.release().catch(() => undefined);
  }
}

/** The browser's wake lock for this document (null where it has none). */
export function createScreenWakeLock(): ScreenWakeLock | null {
  if (typeof document === "undefined" || typeof navigator === "undefined") return null;
  const api = (navigator as Navigator & { wakeLock?: WakeLockApi }).wakeLock ?? null;
  return new ScreenWakeLock(api, document);
}
