"use client";

import { useMemo, useSyncExternalStore } from "react";
import { getAppServices } from "@/lib/app-services";
import type { UploadItem, UploadQueueSnapshot } from "@/lib/offline/upload-queue";

const EMPTY: UploadQueueSnapshot = { items: [] };
const noopUnsubscribe = () => {};

function subscribe(listener: () => void) {
  if (typeof window === "undefined") return noopUnsubscribe;
  return getAppServices().uploads.subscribe(listener);
}

function getSnapshot(): UploadQueueSnapshot {
  return getAppServices().uploads.getSnapshot();
}

function getServerSnapshot(): UploadQueueSnapshot {
  return EMPTY;
}

/** Pending/failed uploads, optionally filtered to one vehicle. */
export function usePendingUploads(vehicleId?: string): readonly UploadItem[] {
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return useMemo(
    () =>
      vehicleId ? snapshot.items.filter((item) => item.vehicleId === vehicleId) : snapshot.items,
    [snapshot, vehicleId],
  );
}

/** Latest pending upload per shot key (the newest capture wins). */
export function indexUploadsByShot(items: readonly UploadItem[]): Map<string, UploadItem> {
  const map = new Map<string, UploadItem>();
  for (const item of items) map.set(item.shotKey, item);
  return map;
}
