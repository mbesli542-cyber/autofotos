"use client";

import { Camera, CircleCheck, Pencil, Plus } from "lucide-react";
import type { ReactNode } from "react";
import { AppHeader } from "@/components/layout/AppHeader";
import { PageContainer } from "@/components/layout/PageContainer";
import { ButtonLink } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { PageLoading } from "@/components/ui/Spinner";
import { VehicleCard } from "@/components/vehicles/VehicleCard";
import { useVehicleSummaries } from "@/hooks/use-vehicle-data";
import type { VehicleSummary } from "@/lib/domain/types";
import { vehicleDisplayName } from "@/lib/format";
import { canProcessVehicle } from "@/lib/vehicles/status";

/** Vehicles that need one more step before they belong to the hub. */
interface HubPendingStep {
  title: string;
  description: string;
  filter: (summary: VehicleSummary) => boolean;
  href: (summary: VehicleSummary) => string;
  actionLabel: string;
}

interface HubConfig {
  title: string;
  heading: string;
  description: string;
  filter: (summary: VehicleSummary) => boolean;
  href: (summary: VehicleSummary) => string;
  emptyIcon: ReactNode;
  emptyTitle: string;
  emptyDescription: string;
  showCreate: boolean;
  pendingStep?: HubPendingStep;
}

const HUBS = {
  camera: {
    title: "Kamera",
    heading: "Aufnahmen",
    description: "Fahrzeuge mit offenen Aufnahmen. Tippen Sie auf ein Fahrzeug, um fortzufahren.",
    filter: (s) => s.capturedRequiredCount < s.requiredCount,
    href: (s) => `/fahrzeuge/${s.vehicle.id}/kamera`,
    emptyIcon: <Camera className="size-7" aria-hidden />,
    emptyTitle: "Keine offenen Aufnahmen",
    emptyDescription: "Alle Fahrzeuge sind vollständig fotografiert.",
    showCreate: true,
  },
  processing: {
    title: "Bearbeiten",
    heading: "Fotos bearbeiten",
    description: "Fahrzeuge mit abgeschlossenen Aufnahmen, bereit für die Bearbeitung.",
    filter: (s) => canProcessVehicle(s.vehicle.status),
    href: (s) => `/fahrzeuge/${s.vehicle.id}/bearbeiten`,
    emptyIcon: <Pencil className="size-7" aria-hidden />,
    emptyTitle: "Noch keine Fahrzeuge bereit",
    emptyDescription: "Sobald die Aufnahmen eines Fahrzeugs abgeschlossen sind, erscheint es hier.",
    showCreate: false,
    // All required photos are there, but "Aufnahmen abschließen" is still missing.
    pendingStep: {
      title: "Bereit zum Abschließen",
      description: "Alle Pflichtfotos sind aufgenommen. Prüfen Sie die Fotos und schließen Sie die Aufnahmen ab, um sie zu bearbeiten.",
      filter: (s) => s.capturedRequiredCount >= s.requiredCount && !canProcessVehicle(s.vehicle.status),
      href: (s) => `/fahrzeuge/${s.vehicle.id}/fotos`,
      actionLabel: "Aufnahmen abschließen",
    },
  },
} satisfies Record<string, HubConfig>;

/** Tab screens "Kamera" and "Bearbeiten": filtered vehicle lists. */
export function VehicleHubScreen({ hub }: { hub: keyof typeof HUBS }) {
  const config: HubConfig = HUBS[hub];
  const { state, reload } = useVehicleSummaries();
  const items = state.status === "ready" ? state.summaries.filter(config.filter) : [];
  const step = config.pendingStep;
  const stepItems = state.status === "ready" && step ? state.summaries.filter(step.filter) : [];

  return (
    <>
      <AppHeader title={config.title} />
      <PageContainer>
        <h2 className="text-2xl font-bold tracking-tight">{config.heading}</h2>
        <p className="mt-1 mb-5 text-sm text-ae-muted">{config.description}</p>

        {config.showCreate && (
          <ButtonLink
            href="/fahrzeuge/neu"
            size="lg"
            fullWidth
            className="mb-6 sm:w-auto"
            icon={<Plus className="size-5" aria-hidden />}
          >
            Neues Fahrzeug erfassen
          </ButtonLink>
        )}

        {step && stepItems.length > 0 && (
          <section
            className="mb-6 rounded-2xl border border-ae-border bg-ae-surface/60 p-4"
            aria-labelledby={`${hub}-pending-step-title`}
          >
            <h3 id={`${hub}-pending-step-title`} className="font-semibold">
              {step.title}
            </h3>
            <p className="mt-1 mb-4 text-sm text-ae-muted">{step.description}</p>
            <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {stepItems.map((summary) => (
                <li key={summary.vehicle.id} className="flex flex-col gap-2">
                  <VehicleCard summary={summary} href={step.href(summary)} />
                  <ButtonLink
                    href={step.href(summary)}
                    variant="secondary"
                    fullWidth
                    icon={<CircleCheck className="size-5" aria-hidden />}
                    aria-label={`${step.actionLabel}: ${vehicleDisplayName(summary.vehicle)}`}
                  >
                    {step.actionLabel}
                  </ButtonLink>
                </li>
              ))}
            </ul>
          </section>
        )}

        {state.status === "loading" && <PageLoading message="Fahrzeuge werden geladen…" />}
        {state.status === "error" && <ErrorState message={state.message} onRetry={reload} />}
        {state.status === "ready" &&
          (items.length === 0 ? (
            <EmptyState
              icon={config.emptyIcon}
              title={config.emptyTitle}
              description={config.emptyDescription}
            />
          ) : (
            <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {items.map((summary) => (
                <li key={summary.vehicle.id}>
                  <VehicleCard summary={summary} href={config.href(summary)} />
                </li>
              ))}
            </ul>
          ))}
      </PageContainer>
    </>
  );
}
