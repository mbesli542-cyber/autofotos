"use client";

import { Save } from "lucide-react";
import { useRouter } from "next/navigation";
import { AppHeader } from "@/components/layout/AppHeader";
import { PageContainer } from "@/components/layout/PageContainer";
import { ErrorState } from "@/components/ui/ErrorState";
import { PageLoading } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { VehicleForm } from "@/components/vehicles/VehicleForm";
import { useVehicleDetail } from "@/hooks/use-vehicle-data";
import { getAppServices } from "@/lib/app-services";
import type { VehicleInput } from "@/lib/domain/types";
import { toUserMessage } from "@/lib/errors";
import { vehicleToFormValues } from "@/lib/vehicles/validation";
import { VehicleNotFound } from "./VehicleNotFound";

export function EditVehicleScreen({ vehicleId }: { vehicleId: string }) {
  const router = useRouter();
  const toast = useToast();
  const { state, reload } = useVehicleDetail(vehicleId);
  const detailHref = `/fahrzeuge/${vehicleId}`;

  async function handleSave(input: VehicleInput) {
    try {
      await getAppServices().backend.data.updateVehicle(vehicleId, input);
      toast.success("Fahrzeugdaten gespeichert.");
      router.push(detailHref);
    } catch (error) {
      throw new Error(toUserMessage(error, "Fahrzeugdaten konnten nicht gespeichert werden."));
    }
  }

  return (
    <>
      <AppHeader title="Fahrzeugdaten" backHref={detailHref} />
      <PageContainer className="max-w-2xl">
        {state.status === "loading" && <PageLoading message="Fahrzeug wird geladen…" />}
        {state.status === "error" && <ErrorState message={state.message} onRetry={reload} />}
        {state.status === "not_found" && <VehicleNotFound />}
        {state.status === "ready" && (
          <VehicleForm
            key={state.vehicle.updatedAt}
            initialValues={vehicleToFormValues(state.vehicle)}
            submitLabel="Änderungen speichern"
            loadingText="Wird gespeichert…"
            submitIcon={<Save className="size-5" aria-hidden />}
            onSubmit={handleSave}
          />
        )}
      </PageContainer>
    </>
  );
}
