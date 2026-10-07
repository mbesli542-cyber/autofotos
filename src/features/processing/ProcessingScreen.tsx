"use client";

import { CircleCheck, Images, Info, ShieldCheck, WandSparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { AppHeader } from "@/components/layout/AppHeader";
import { PageContainer, StickyActions } from "@/components/layout/PageContainer";
import { ProcessingJobList } from "@/components/processing/ProcessingJobList";
import { ProcessingPresetCard } from "@/components/processing/ProcessingPresetCard";
import { Button, ButtonLink } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { LoadingButton } from "@/components/ui/LoadingButton";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { PageLoading } from "@/components/ui/Spinner";
import { useVehicleDetail } from "@/hooks/use-vehicle-data";
import type { ProcessingPresetId } from "@/lib/domain/types";
import { vehicleDisplayName, pluralizePhotos } from "@/lib/format";
import { PROCESSING_IS_SIMULATED } from "@/lib/processing/client-config";
import { DEFAULT_PROCESSING_PRESET, PROCESSING_PRESET_LIST } from "@/lib/processing/presets";
import { canProcessVehicle } from "@/lib/vehicles/status";
import { VehicleNotFound } from "@/features/vehicles/VehicleNotFound";
import { useProcessingRun } from "./use-processing-run";

/** "Fotos bearbeiten": choose a style and start (currently simulated) processing. */
export function ProcessingScreen({ vehicleId }: { vehicleId: string }) {
  const { state, reload } = useVehicleDetail(vehicleId);
  const run = useProcessingRun(vehicleId);
  const [preset, setPreset] = useState<ProcessingPresetId>(DEFAULT_PROCESSING_PRESET);
  const detailHref = `/fahrzeuge/${vehicleId}`;

  const photos = useMemo(() => (state.status === "ready" ? state.photos : []), [state]);
  const finished = run.items.filter((item) => item.status === "complete" || item.status === "failed").length;
  const failed = run.items.filter((item) => item.status === "failed").length;

  if (state.status !== "ready") {
    return (
      <>
        <AppHeader title="Fotos bearbeiten" backHref={detailHref} />
        <PageContainer>
          {state.status === "loading" && <PageLoading message="Fotos werden geladen…" />}
          {state.status === "error" && <ErrorState message={state.message} onRetry={reload} />}
          {state.status === "not_found" && <VehicleNotFound />}
        </PageContainer>
      </>
    );
  }

  const { vehicle } = state;

  if (!canProcessVehicle(vehicle.status)) {
    return (
      <>
        <AppHeader title="Fotos bearbeiten" backHref={detailHref} />
        <PageContainer>
          <EmptyState
            icon={<Images className="size-7" aria-hidden />}
            title="Aufnahmen noch nicht abgeschlossen"
            description="Bitte nehmen Sie alle Pflichtfotos auf und schließen Sie die Aufnahmen ab. Danach können die Fotos bearbeitet werden."
            action={
              <ButtonLink href={`/fahrzeuge/${vehicleId}/fotos`} fullWidth>
                Fotos überprüfen
              </ButtonLink>
            }
          />
        </PageContainer>
      </>
    );
  }

  return (
    <>
      <AppHeader title="Fotos bearbeiten" backHref={detailHref} />
      <PageContainer className="max-w-3xl">
        <p className="text-sm text-ae-muted">{vehicleDisplayName(vehicle)}</p>
        <h2 className="mt-0.5 text-2xl font-bold tracking-tight">Fotos bearbeiten</h2>

        {run.phase === "idle" ? (
          <>
            <section className="mt-5" aria-labelledby="preset-title">
              <h3 id="preset-title" className="mb-3 text-sm font-semibold text-ae-muted">
                Bearbeitungsstil wählen
              </h3>
              <div role="radiogroup" aria-labelledby="preset-title" className="flex flex-col gap-3">
                {PROCESSING_PRESET_LIST.map((item) => (
                  <ProcessingPresetCard
                    key={item.id}
                    preset={item}
                    selected={preset === item.id}
                    onSelect={() => setPreset(item.id)}
                  />
                ))}
              </div>
            </section>

            <div className="mt-5 flex gap-3 rounded-xl border border-ae-border bg-ae-surface p-4 text-sm">
              <ShieldCheck className="size-5 shrink-0 text-ae-success" aria-hidden />
              <p className="text-ae-muted">
                <span className="font-semibold text-ae-text">Das Fahrzeug bleibt original.</span> Farbe, Felgen,
                Embleme, Scheinwerfer, Innenraum und Gebrauchsspuren werden nie künstlich erzeugt oder
                verändert. Originalfotos bleiben unverändert erhalten.
              </p>
            </div>

            {PROCESSING_IS_SIMULATED && (
              <div className="mt-3 flex gap-3 rounded-xl border border-ae-blue/30 bg-ae-blue-soft p-4 text-sm">
                <Info className="size-5 shrink-0 text-ae-blue" aria-hidden />
                <p className="text-ae-muted">
                  <span className="font-semibold text-ae-text">Vorschau-Version:</span> Die automatische
                  Showroom-Bearbeitung ist noch nicht angebunden. Es wird eine gekennzeichnete Vorschau mit
                  AutoExperten-Branding erzeugt.
                </p>
              </div>
            )}

            <StickyActions>
              <LoadingButton
                size="lg"
                fullWidth
                className="sm:w-auto sm:min-w-72"
                loading={false}
                onClick={() => void run.start(photos, preset)}
                disabled={photos.length === 0}
                icon={<WandSparkles className="size-5" aria-hidden />}
              >
                Fotos verarbeiten
              </LoadingButton>
              <p className="mt-2 text-center text-xs text-ae-subtle sm:text-left">
                {pluralizePhotos(photos.length)} werden verarbeitet.
              </p>
            </StickyActions>
          </>
        ) : (
          <section className="mt-5" aria-live="polite">
            {run.phase === "done" ? (
              <div className="mb-4 flex flex-col items-center rounded-2xl border border-ae-success/30 bg-ae-success/8 p-6 text-center">
                <CircleCheck className="size-10 text-ae-success" aria-hidden />
                <h3 className="mt-2 text-lg font-semibold">
                  {failed === 0 ? "Bearbeitung abgeschlossen" : "Bearbeitung mit Fehlern abgeschlossen"}
                </h3>
                <p className="mt-1 text-sm text-ae-muted">
                  {finished - failed} von {run.items.length} Fotos bearbeitet
                  {failed > 0 ? ` · ${failed} fehlgeschlagen` : ""}.
                </p>
                <div className="mt-4 grid w-full max-w-sm gap-2">
                  <ButtonLink href={`${detailHref}?ansicht=bearbeitet`} size="lg" fullWidth>
                    Ergebnisse ansehen
                  </ButtonLink>
                  <Button variant="ghost" onClick={run.reset}>
                    Anderen Stil wählen
                  </Button>
                </div>
              </div>
            ) : (
              <div className="mb-4 rounded-2xl border border-ae-border bg-ae-surface p-4">
                <div className="mb-2 flex items-baseline justify-between text-sm">
                  <span className="font-semibold">Fotos werden verarbeitet…</span>
                  <span className="text-ae-muted tabular-nums">
                    {finished} / {run.items.length}
                  </span>
                </div>
                <ProgressBar value={finished} max={run.items.length} label="Bearbeitungsfortschritt" />
                <p className="mt-2 text-xs text-ae-subtle">
                  Bitte lassen Sie die App geöffnet, bis die Bearbeitung abgeschlossen ist.
                </p>
              </div>
            )}
            <ProcessingJobList items={run.items} />
          </section>
        )}
      </PageContainer>
    </>
  );
}
