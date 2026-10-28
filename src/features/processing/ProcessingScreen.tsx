"use client";

import { CircleCheck, Images, KeyRound, ShieldCheck, TriangleAlert, WandSparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { AppHeader } from "@/components/layout/AppHeader";
import { PageContainer, StickyActions } from "@/components/layout/PageContainer";
import { AccessCodeForm } from "@/components/processing/AccessCodeForm";
import { ProcessingJobList, type JobRetakeAction } from "@/components/processing/ProcessingJobList";
import { ProcessingPresetCard } from "@/components/processing/ProcessingPresetCard";
import {
  ProcessingStatusPanel,
  type ProcessorConnectionState,
} from "@/components/processing/ProcessingStatusPanel";
import { Button, ButtonLink } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { LoadingButton } from "@/components/ui/LoadingButton";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { PageLoading } from "@/components/ui/Spinner";
import { useProcessingAccessCode } from "@/hooks/use-processing-access-code";
import { useProcessingStatus } from "@/hooks/use-processing-status";
import { usePendingUploads } from "@/hooks/use-upload-queue";
import { useVehicleDetail } from "@/hooks/use-vehicle-data";
import { getAppServices, getShotTemplate } from "@/lib/app-services";
import { cameraHref } from "@/lib/camera/camera-links";
import { cn } from "@/lib/cn";
import type { ProcessingPresetId, VehiclePhotoWithUrls } from "@/lib/domain/types";
import { vehicleDisplayName, pluralizePhotos } from "@/lib/format";
import { storeAccessCode } from "@/lib/processing/access-code-storage";
import { PROCESSOR_UI_HINT } from "@/lib/processing/client-config";
import {
  DEFAULT_PROCESSING_PRESET,
  PROCESSING_PRESET_LIST,
  isProcessingPresetAvailable,
} from "@/lib/processing/presets";
import { needsResultCheck } from "@/lib/processing/processing-warnings";
import { getRetakeStatus } from "@/lib/processing/quality-gate";
import { PROCESSING_BLOCKER_MESSAGES, getProcessingBlocker } from "@/lib/processing/run-blockers";
import { countByTreatment, selectPhotosForProcessing } from "@/lib/processing/shot-treatment";
import { canStartProcessing } from "@/lib/processing/types";
import { canProcessVehicle } from "@/lib/vehicles/status";
import { VehicleNotFound } from "@/features/vehicles/VehicleNotFound";
import { useProcessingRun, type PhotoRunState, type RunResult } from "./use-processing-run";

/** "Fotos bearbeiten": choose a style and process the photos with the real showroom processor. */
export function ProcessingScreen({ vehicleId }: { vehicleId: string }) {
  const template = getShotTemplate();
  const { state, reload } = useVehicleDetail(vehicleId);
  const processing = useProcessingStatus();
  const storedAccessCode = useProcessingAccessCode();
  const pendingUploads = usePendingUploads(vehicleId);
  const run = useProcessingRun(vehicleId);
  const [chosenPreset, setChosenPreset] = useState<ProcessingPresetId>(DEFAULT_PROCESSING_PRESET);
  // Only styles the processor implements can ever be the selected one.
  const preset = isProcessingPresetAvailable(chosenPreset) ? chosenPreset : DEFAULT_PROCESSING_PRESET;
  const [accessCodeError, setAccessCodeError] = useState<string | null>(null);
  /** Preset of a run that was refused because of the access code – retried after entering it. */
  const [retryPreset, setRetryPreset] = useState<ProcessingPresetId | null>(null);
  const detailHref = `/fahrzeuge/${vehicleId}`;

  const photos = useMemo(
    () => (state.status === "ready" ? selectPhotosForProcessing(template, state.photos) : []),
    [state, template],
  );
  const counts = useMemo(() => countByTreatment(template, photos), [template, photos]);
  const finished = run.items.filter((item) => item.status === "complete" || item.status === "failed").length;
  /** Shots a new photo would fix (quality gate etc.) or replaced meanwhile, by shot key. */
  const retakes = useMemo(
    () =>
      new Map(
        run.items.flatMap((item) => {
          const retake = getRetakeStatus(item, photos, pendingUploads);
          return retake ? [[item.shotKey, retake] as const] : [];
        }),
      ),
    [run.items, photos, pendingUploads],
  );
  /** A new photo of the shot (being saved or saved) – its result or failure no longer applies. */
  const hasNewPhoto = (item: PhotoRunState) => {
    const kind = retakes.get(item.shotKey)?.kind;
    return kind === "saving" || kind === "ready";
  };
  const newPhotos = run.items.filter(hasNewPhoto).length;
  const failed = run.items.filter((item) => item.status === "failed" && !hasNewPhoto(item)).length;
  const succeeded = run.items.filter((item) => item.status === "complete" && !hasNewPhoto(item)).length;
  /** Saved results the processor asks to check (or whose photo should be retaken). */
  const toCheck = run.items.filter(
    (item) => item.status === "complete" && !hasNewPhoto(item) && needsResultCheck(item.warnings),
  ).length;
  /** Rejected shots still waiting for a new photo. */
  const rejected = run.items.filter(
    (item) => item.status === "failed" && retakes.get(item.shotKey)?.kind === "retake",
  ).length;

  const status = processing.state.status;
  const connection: ProcessorConnectionState =
    processing.state.phase === "loading"
      ? PROCESSOR_UI_HINT === "real"
        ? "checking"
        : "disconnected"
      : status?.connected
        ? "connected"
        : "disconnected";
  const needsAccessCode = Boolean(status?.accessCodeRequired) && (!storedAccessCode || accessCodeError !== null);
  const blocker = getProcessingBlocker(photos, pendingUploads);
  const canStart = canStartProcessing(status) && photos.length > 0 && !needsAccessCode && blocker === null;
  /** New photos (retakes) that can be processed now. */
  const readyPhotos = [...retakes.values()].flatMap((retake) => (retake.kind === "ready" ? [retake.photo] : []));

  function handleRunResult(result: RunResult, selectedPreset: ProcessingPresetId) {
    if (result.outcome === "access_denied") {
      storeAccessCode(null);
      setAccessCodeError(result.message ?? "Der Zugangscode für die Bildbearbeitung ist ungültig.");
      setRetryPreset(selectedPreset);
    } else if (result.outcome === "done") {
      processing.refresh();
    }
  }

  async function startRun(selectedPreset: ProcessingPresetId, accessCode: string | null) {
    setAccessCodeError(null);
    handleRunResult(await run.start(photos, selectedPreset, accessCode), selectedPreset);
  }

  /**
   * Processes shots again with the run's style: their new photos after
   * "Foto neu aufnehmen", or the same photo after a temporary failure.
   */
  async function processAgain(targets: readonly VehiclePhotoWithUrls[]) {
    const runPreset = run.preset ?? preset;
    setAccessCodeError(null);
    handleRunResult(await run.retry(targets, runPreset, storedAccessCode), runPreset);
  }

  function retakeAction(item: PhotoRunState): JobRetakeAction | null {
    const retake = retakes.get(item.shotKey);
    if (!retake) {
      // Connection lost, processor busy, …: the same photo can simply be processed again.
      const photo = item.status === "failed" ? photos.find((entry) => entry.id === item.photoId) : undefined;
      return photo ? { kind: "retry", onRetry: () => void processAgain([photo]) } : null;
    }
    if (retake.kind === "retake") {
      return { kind: "retake", href: cameraHref(vehicleId, { shot: item.shotKey, returnTo: "bearbeiten" }) };
    }
    if (retake.kind === "saving") {
      return {
        kind: "saving",
        failed: retake.failed,
        error: retake.error,
        onRetryUpload: () => getAppServices().uploads.retry(retake.uploadId),
      };
    }
    return { kind: "ready", onProcess: () => void processAgain([retake.photo]) };
  }

  function handleAccessCode(code: string) {
    storeAccessCode(code);
    setAccessCodeError(null);
    if (retryPreset && canStartProcessing(status)) {
      setRetryPreset(null);
      void startRun(retryPreset, code);
    }
  }

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

        <div className="mt-4">
          <ProcessingStatusPanel
            state={connection}
            showroomError={connection === "connected" ? (status?.showroomError ?? null) : null}
            connectionError={status?.connectionError ?? null}
          />
        </div>

        {run.phase === "idle" ? (
          <>
            {connection === "connected" && status?.accessCodeRequired && (
              <div className="mt-4">
                {needsAccessCode ? (
                  <AccessCodeForm
                    error={accessCodeError}
                    submitLabel={retryPreset ? "Speichern & erneut versuchen" : "Speichern"}
                    onSubmit={handleAccessCode}
                  />
                ) : (
                  <p className="flex items-center gap-2 text-sm text-ae-muted">
                    <KeyRound className="size-4 text-ae-subtle" aria-hidden />
                    Zugangscode auf diesem Gerät gespeichert.
                    <button
                      type="button"
                      className="font-semibold text-ae-blue"
                      onClick={() => storeAccessCode(null)}
                    >
                      Ändern
                    </button>
                  </p>
                )}
              </div>
            )}

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
                    onSelect={() => setChosenPreset(item.id)}
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

            <StickyActions>
              <LoadingButton
                size="lg"
                fullWidth
                className="sm:w-auto sm:min-w-72"
                loading={false}
                onClick={() => void startRun(preset, storedAccessCode)}
                disabled={!canStart}
                icon={<WandSparkles className="size-5" aria-hidden />}
              >
                Fotos verarbeiten
              </LoadingButton>
              {blocker && (
                <p className="mt-2 text-center text-sm font-medium text-ae-warning sm:text-left" role="status">
                  {PROCESSING_BLOCKER_MESSAGES[blocker]}
                </p>
              )}
              <p className="mt-2 text-center text-xs text-ae-subtle sm:text-left">
                {pluralizePhotos(photos.length)}: {counts.showroom} im AutoExperten Showroom,{" "}
                {counts.original_environment} Innenraum/Detail in Originalumgebung.
              </p>
            </StickyActions>
          </>
        ) : (
          <section className="mt-5" aria-live="polite">
            {run.phase === "done" ? (
              <RunSummary
                total={run.items.length}
                succeeded={succeeded}
                failed={failed}
                newPhotos={newPhotos}
                retakes={rejected}
                toCheck={toCheck}
                readyPhotos={readyPhotos.length}
                resultsHref={`${detailHref}?ansicht=bearbeitet`}
                onProcessReady={() => void processAgain(readyPhotos)}
                onReset={run.reset}
              />
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
            <ProcessingJobList items={run.items} retakeAction={retakeAction} />
          </section>
        )}
      </PageContainer>
    </>
  );
}

function RunSummary({
  total,
  succeeded,
  failed,
  newPhotos,
  retakes,
  toCheck,
  readyPhotos,
  resultsHref,
  onProcessReady,
  onReset,
}: {
  total: number;
  /** Shots whose current photo was processed. */
  succeeded: number;
  /** Failed shots without a new photo. */
  failed: number;
  /** Shots with a new photo (being saved or saved) that is not processed yet. */
  newPhotos: number;
  /** Failed shots still waiting for a new photo ("Foto neu aufnehmen" in the list). */
  retakes: number;
  /** Processed shots whose result the processor asks to check (hints in the list). */
  toCheck: number;
  /** New photos (retakes) that are saved and not processed yet. */
  readyPhotos: number;
  resultsHref: string;
  onProcessReady: () => void;
  onReset: () => void;
}) {
  const nothingProcessed = succeeded === 0;
  const tone =
    nothingProcessed && newPhotos === 0
      ? "danger"
      : failed > 0 || newPhotos > 0 || toCheck > 0
        ? "warning"
        : "success";
  const newPhotosText = newPhotos === 1 ? "1 neues Foto" : `${newPhotos} neue Fotos`;
  return (
    <div
      className={cn(
        "mb-4 flex flex-col items-center rounded-2xl border p-6 text-center",
        tone === "danger" && "border-ae-danger/30 bg-ae-danger/8",
        tone === "warning" && "border-ae-warning/30 bg-ae-warning/8",
        tone === "success" && "border-ae-success/30 bg-ae-success/8",
      )}
    >
      {tone === "success" ? (
        <CircleCheck className="size-10 text-ae-success" aria-hidden />
      ) : (
        <TriangleAlert className={cn("size-10", tone === "danger" ? "text-ae-danger" : "text-ae-warning")} aria-hidden />
      )}
      <h3 className="mt-2 text-lg font-semibold">
        {tone === "danger"
          ? "Bearbeitung fehlgeschlagen"
          : failed === 0
            ? "Bearbeitung abgeschlossen"
            : "Bearbeitung mit Fehlern abgeschlossen"}
      </h3>
      <p className="mt-1 text-sm text-ae-muted">
        {succeeded} von {total} Fotos bearbeitet
        {failed > 0 ? ` · ${failed} fehlgeschlagen` : ""}
        {newPhotos > 0 ? ` · ${newPhotosText} noch nicht bearbeitet` : ""}.
        {failed > 0 ? " Für fehlgeschlagene Fotos wurde nichts gespeichert." : ""}
      </p>
      {retakes > 0 && (
        <p className="mt-1 text-sm text-ae-muted">
          {retakes === 1
            ? "Ein Foto muss neu aufgenommen werden – siehe Hinweis in der Liste."
            : `${retakes} Fotos müssen neu aufgenommen werden – siehe Hinweise in der Liste.`}
        </p>
      )}
      {toCheck > 0 && (
        <p className="mt-1 text-sm text-ae-muted">
          {toCheck === 1
            ? "Bitte ein Ergebnis prüfen – siehe Hinweis in der Liste."
            : `Bitte ${toCheck} Ergebnisse prüfen – siehe Hinweise in der Liste.`}
        </p>
      )}
      <div className="mt-4 grid w-full max-w-sm gap-2">
        {readyPhotos > 0 && (
          <Button size="lg" fullWidth onClick={onProcessReady} icon={<WandSparkles className="size-5" aria-hidden />}>
            {readyPhotos === 1
              ? "Neues Foto bereit – jetzt bearbeiten"
              : `${readyPhotos} neue Fotos bereit – jetzt bearbeiten`}
          </Button>
        )}
        {!nothingProcessed && (
          <ButtonLink href={resultsHref} size="lg" variant={readyPhotos > 0 ? "secondary" : undefined} fullWidth>
            Ergebnisse ansehen
          </ButtonLink>
        )}
        <Button variant={nothingProcessed ? "secondary" : "ghost"} onClick={onReset}>
          {nothingProcessed ? "Erneut versuchen" : "Anderen Stil wählen"}
        </Button>
      </div>
    </div>
  );
}
