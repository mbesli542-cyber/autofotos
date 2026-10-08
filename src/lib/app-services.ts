/**
 * Client-side service container (lazy singleton).
 *
 * Created on first use in the browser – never during server rendering –
 * so no browser API is touched on the server. Components access it only in
 * effects, event handlers and external-store subscriptions.
 */
import { AuthStore } from "@/lib/auth/auth-store";
import { CAMERA_CONFIG } from "@/lib/camera/config";
import { getDataBackendMode } from "@/lib/data/backend-mode";
import { createBackend } from "@/lib/data/create-backend";
import type { Backend, BackendMode } from "@/lib/data/types";
import { UploadQueue } from "@/lib/offline/upload-queue";
import { DEFAULT_SHOT_TEMPLATE, type ShotTemplate } from "@/lib/shots/shot-template";
import { savePhotoAndSyncStatus } from "@/lib/workflow/vehicle-workflow";

export interface AppServices {
  backend: Backend;
  template: ShotTemplate;
  uploads: UploadQueue;
  authStore: AuthStore;
}

let services: AppServices | null = null;

export function getAppServices(): AppServices {
  if (typeof window === "undefined") {
    throw new Error("getAppServices() is only available in the browser.");
  }
  if (services) return services;

  const backend = createBackend();
  const template = DEFAULT_SHOT_TEMPLATE;
  const uploads = new UploadQueue(async (record, prepared) => {
    await savePhotoAndSyncStatus(backend.data, template, {
      photoId: record.id,
      vehicleId: record.vehicleId,
      shotKey: record.shotKey,
      shotOrder: record.shotOrder,
      title: record.title,
      file: record.file,
      thumbnail: prepared.thumbnail,
      width: prepared.width,
      height: prepared.height,
      takenAt: record.takenAt,
    });
  }, CAMERA_CONFIG.thumbnailMaxEdge);

  services = { backend, template, uploads, authStore: new AuthStore(backend.auth) };
  return services;
}

/**
 * Safe during render (server and client): derived from env only
 * (NEXT_PUBLIC_DATA_BACKEND + Supabase env, see src/lib/data/backend-mode.ts).
 */
export function getBackendMode(): BackendMode {
  return getDataBackendMode();
}

/** The active shot template (pure, safe during render). */
export function getShotTemplate(): ShotTemplate {
  return DEFAULT_SHOT_TEMPLATE;
}
