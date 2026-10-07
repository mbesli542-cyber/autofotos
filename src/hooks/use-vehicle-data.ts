"use client";

import { useCallback, useEffect, useState } from "react";
import { getAppServices } from "@/lib/app-services";
import type { DataProvider } from "@/lib/data/types";
import type { Vehicle, VehiclePhotoWithUrls, VehicleSummary } from "@/lib/domain/types";
import { toUserMessage } from "@/lib/errors";
import {
  countCapturedRequired,
  getCapturedShotKeys,
  sortPhotosByShotOrder,
} from "@/lib/shots/shot-progress";
import { getRequiredShots, type ShotTemplate } from "@/lib/shots/shot-template";
import { getCoverPhoto } from "@/lib/vehicles/summary";

/* ------------------------------------------------------------------------ */
/* Loaders (plain async functions)                                           */
/* ------------------------------------------------------------------------ */

export async function loadVehicleSummaries(
  data: DataProvider,
  template: ShotTemplate,
): Promise<VehicleSummary[]> {
  const items = await data.listVehicles();
  const covers = items.flatMap(({ photos }) => {
    const cover = getCoverPhoto(template, photos);
    return cover ? [cover] : [];
  });
  const resolved = await data.resolvePhotoUrls(covers);
  const thumbnails = new Map(resolved.map((photo) => [photo.vehicleId, photo.urls.thumbnail]));
  const requiredCount = getRequiredShots(template).length;

  return items.map(({ vehicle, photos }) => ({
    vehicle,
    capturedRequiredCount: countCapturedRequired(template, getCapturedShotKeys(photos)),
    requiredCount,
    thumbnailUrl: thumbnails.get(vehicle.id) ?? null,
  }));
}

export type VehicleDetailState =
  | { status: "loading" }
  | { status: "not_found" }
  | { status: "error"; message: string }
  | { status: "ready"; vehicle: Vehicle; photos: VehiclePhotoWithUrls[] };

async function loadVehicleDetail(
  data: DataProvider,
  template: ShotTemplate,
  vehicleId: string,
): Promise<VehicleDetailState> {
  const vehicle = await data.getVehicle(vehicleId);
  if (!vehicle) return { status: "not_found" };
  const photos = await data.listPhotos(vehicleId);
  const withUrls = await data.resolvePhotoUrls(sortPhotosByShotOrder(template, photos));
  return { status: "ready", vehicle, photos: withUrls };
}

/* ------------------------------------------------------------------------ */
/* Hooks                                                                     */
/* ------------------------------------------------------------------------ */

export type VehicleListState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; summaries: VehicleSummary[] };

export function useVehicleSummaries() {
  const [state, setState] = useState<VehicleListState>({ status: "loading" });
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let active = true;
    const { backend, template } = getAppServices();
    loadVehicleSummaries(backend.data, template).then(
      (summaries) => {
        if (active) setState({ status: "ready", summaries });
      },
      (error: unknown) => {
        if (active) setState({ status: "error", message: toUserMessage(error) });
      },
    );
    return () => {
      active = false;
    };
  }, [version]);

  // Refresh when a background upload finished.
  useEffect(() => getAppServices().uploads.onUploaded(() => setVersion((v) => v + 1)), []);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { state, reload };
}

export function useVehicleDetail(vehicleId: string) {
  const [state, setState] = useState<VehicleDetailState>({ status: "loading" });
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let active = true;
    const { backend, template } = getAppServices();
    loadVehicleDetail(backend.data, template, vehicleId).then(
      (next) => {
        if (active) setState(next);
      },
      (error: unknown) => {
        if (!active) return;
        // Keep showing loaded data if only a refresh failed.
        setState((previous) =>
          previous.status === "ready" ? previous : { status: "error", message: toUserMessage(error) },
        );
      },
    );
    return () => {
      active = false;
    };
  }, [vehicleId, version]);

  useEffect(
    () =>
      getAppServices().uploads.onUploaded((item) => {
        if (item.vehicleId === vehicleId) setVersion((v) => v + 1);
      }),
    [vehicleId],
  );

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { state, reload };
}
