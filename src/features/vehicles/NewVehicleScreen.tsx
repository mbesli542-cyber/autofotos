"use client";

import { Camera } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AppHeader } from "@/components/layout/AppHeader";
import { PageContainer } from "@/components/layout/PageContainer";
import { VehicleForm } from "@/components/vehicles/VehicleForm";
import { getAppServices } from "@/lib/app-services";
import type { VehicleInput } from "@/lib/domain/types";
import { toUserMessage } from "@/lib/errors";

export function NewVehicleScreen() {
  const router = useRouter();
  // New key → fresh form next time (routes stay mounted between navigations).
  const [formKey, setFormKey] = useState(0);

  async function handleCreate(input: VehicleInput) {
    try {
      const vehicle = await getAppServices().backend.data.createVehicle(input);
      router.push(`/fahrzeuge/${vehicle.id}/kamera`);
      setFormKey((key) => key + 1);
    } catch (error) {
      throw new Error(toUserMessage(error, "Fahrzeug konnte nicht erstellt werden. Bitte versuchen Sie es erneut."));
    }
  }

  return (
    <>
      <AppHeader title="Neues Fahrzeug" backHref="/fahrzeuge" />
      <PageContainer className="max-w-2xl">
        <h2 className="text-2xl font-bold tracking-tight">Neues Fahrzeug erfassen</h2>
        <p className="mt-1 mb-6 text-sm text-ae-muted">
          Nur Hersteller und Modell sind erforderlich. Danach starten die geführten Aufnahmen.
        </p>
        <VehicleForm
          key={formKey}
          submitLabel="Fahrzeug erstellen & Fotos aufnehmen"
          loadingText="Fahrzeug wird erstellt…"
          submitIcon={<Camera className="size-5" aria-hidden />}
          onSubmit={handleCreate}
        />
      </PageContainer>
    </>
  );
}
