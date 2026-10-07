"use client";

import { CameraCapture } from "@/components/camera/CameraCapture";
import { ErrorState } from "@/components/ui/ErrorState";
import { Spinner } from "@/components/ui/Spinner";
import { useVehicleDetail } from "@/hooks/use-vehicle-data";
import { getShotTemplate } from "@/lib/app-services";
import { VehicleNotFound } from "@/features/vehicles/VehicleNotFound";

export function CameraScreen({
  vehicleId,
  initialShotKey,
  returnTo,
}: {
  vehicleId: string;
  initialShotKey: string | null;
  returnTo: "fotos" | null;
}) {
  const { state, reload } = useVehicleDetail(vehicleId);

  if (state.status === "ready") {
    return (
      <CameraCapture
        // Remount when the requested shot changes (routes stay mounted).
        key={`${initialShotKey ?? "auto"}-${returnTo ?? ""}`}
        vehicle={state.vehicle}
        photos={state.photos}
        template={getShotTemplate()}
        initialShotKey={initialShotKey}
        returnTo={returnTo}
      />
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black p-6 text-white">
      {state.status === "loading" && (
        <div className="flex flex-col items-center gap-3 text-sm text-white/80" role="status">
          <Spinner className="size-7 text-ae-blue" />
          Kamera wird vorbereitet…
        </div>
      )}
      {state.status === "error" && <ErrorState message={state.message} onRetry={reload} className="w-full max-w-sm" />}
      {state.status === "not_found" && (
        <div className="w-full max-w-sm">
          <VehicleNotFound />
        </div>
      )}
    </div>
  );
}
