"use client";

import { Camera, Pencil, Plus } from "lucide-react";
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
import { canProcessVehicle } from "@/lib/vehicles/status";

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
  },
} satisfies Record<string, HubConfig>;

/** Tab screens "Kamera" and "Bearbeiten": filtered vehicle lists. */
export function VehicleHubScreen({ hub }: { hub: keyof typeof HUBS }) {
  const config: HubConfig = HUBS[hub];
  const { state, reload } = useVehicleSummaries();
  const items = state.status === "ready" ? state.summaries.filter(config.filter) : [];

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
