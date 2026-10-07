"use client";

import { CarFront, Plus } from "lucide-react";
import { AppHeader } from "@/components/layout/AppHeader";
import { PageContainer } from "@/components/layout/PageContainer";
import { ButtonLink } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { PageLoading } from "@/components/ui/Spinner";
import { VehicleCard } from "@/components/vehicles/VehicleCard";
import { useVehicleSummaries } from "@/hooks/use-vehicle-data";

export function VehicleListScreen() {
  const { state, reload } = useVehicleSummaries();

  return (
    <>
      <AppHeader />
      <PageContainer>
        <div className="mb-4 flex items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Fahrzeuge</h1>
            {state.status === "ready" && (
              <p className="mt-0.5 text-sm text-ae-muted">
                {state.summaries.length === 1
                  ? "1 Fahrzeug"
                  : `${state.summaries.length} Fahrzeuge`}
              </p>
            )}
          </div>
        </div>

        <ButtonLink
          href="/fahrzeuge/neu"
          size="lg"
          fullWidth
          icon={<Plus className="size-5" aria-hidden />}
          className="mb-6 sm:w-auto"
        >
          Neues Fahrzeug erfassen
        </ButtonLink>

        {state.status === "loading" && <PageLoading message="Fahrzeuge werden geladen…" />}
        {state.status === "error" && <ErrorState message={state.message} onRetry={reload} />}
        {state.status === "ready" &&
          (state.summaries.length === 0 ? (
            <EmptyState
              icon={<CarFront className="size-7" aria-hidden />}
              title="Noch keine Fahrzeuge"
              description="Erfassen Sie das erste Fahrzeug und starten Sie die geführten Aufnahmen."
            />
          ) : (
            <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3" aria-label="Fahrzeugliste">
              {state.summaries.map((summary) => (
                <li key={summary.vehicle.id}>
                  <VehicleCard summary={summary} />
                </li>
              ))}
            </ul>
          ))}
      </PageContainer>
    </>
  );
}
