"use client";

import { useSyncExternalStore } from "react";
import { isTouchDevice, sharePlatform, type SharePlatform } from "@/lib/export/device-save";

const QUERIES = ["(pointer: coarse)", "(hover: none)"] as const;

function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => {};
  const lists = QUERIES.map((query) => window.matchMedia(query));
  for (const list of lists) list.addEventListener("change", onChange);
  return () => {
    for (const list of lists) list.removeEventListener("change", onChange);
  };
}

/**
 * A phone or tablet (coarse pointer, no hover): photos are saved through the
 * share sheet there, downloaded elsewhere. false on the server.
 */
export function useTouchDevice(): boolean {
  return useSyncExternalStore(subscribe, () => isTouchDevice(), () => false);
}

/** The system never changes while the page is open. */
const subscribeNever = () => () => {};

/** The phone's system for the share hints (iOS / Android / other); "other" on the server. */
export function useSharePlatform(): SharePlatform {
  return useSyncExternalStore(subscribeNever, () => sharePlatform(), () => "other");
}
