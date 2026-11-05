"use client";

import { useSyncExternalStore } from "react";
import { isTouchDevice } from "@/lib/export/device-save";

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
