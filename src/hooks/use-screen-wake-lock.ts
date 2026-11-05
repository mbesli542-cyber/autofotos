"use client";

import { useEffect } from "react";
import { createScreenWakeLock } from "@/lib/device/screen-wake-lock";

/**
 * Keeps the screen on while `active` and the page is shown (routes stay
 * mounted: a hidden route runs its cleanup and lets the screen turn off).
 */
export function useScreenWakeLock(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const lock = createScreenWakeLock();
    lock?.start();
    return () => lock?.stop();
  }, [active]);
}
