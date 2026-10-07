"use client";

import { CircleCheck, ImagePlus, Pencil } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useRef, useState, type ChangeEvent } from "react";
import { AppHeader } from "@/components/layout/AppHeader";
import { PageContainer, StickyActions } from "@/components/layout/PageContainer";
import { PhotoGrid } from "@/components/photos/PhotoGrid";
import { PhotoViewer } from "@/components/photos/PhotoViewer";
import { Button, ButtonLink } from "@/components/ui/Button";
import { ErrorState } from "@/components/ui/ErrorState";
import { LoadingButton } from "@/components/ui/LoadingButton";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { PageLoading } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { usePendingUploads } from "@/hooks/use-upload-queue";
import { useVehicleDetail } from "@/hooks/use-vehicle-data";
import { getAppServices, getShotTemplate } from "@/lib/app-services";
import { toUserMessage } from "@/lib/errors";
import { vehicleDisplayName } from "@/lib/format";
import { buildPhotoSlots, type PhotoSlot } from "@/lib/photos/photo-slots";
import { getShotProgress } from "@/lib/shots/shot-progress";
import { getNextExtraShot } from "@/lib/shots/shot-template";
import { canProcessVehicle } from "@/lib/vehicles/status";
import { completeCapture, deletePhotoAndSyncStatus } from "@/lib/workflow/vehicle-workflow";
import { VehicleNotFound } from "@/features/vehicles/VehicleNotFound";

/** "Fotos überprüfen": all shots in template order, retake/delete, complete. */
export function PhotoReviewScreen({ vehicleId }: { vehicleId: string }) {
  const router = useRouter();
  const toast = useToast();
  const template = getShotTemplate();
  const { state, reload } = useVehicleDetail(vehicleId);
  const pending = usePendingUploads(vehicleId);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [completing, setCompleting] = useState(false);

  const photos = useMemo(() => (state.status === "ready" ? state.photos : []), [state]);
  const slots = useMemo(() => buildPhotoSlots(template, photos, pending), [template, photos, pending]);
  const allSlots = useMemo(() => [...slots.required, ...slots.extras], [slots]);
  const savedKeys = useMemo(() => new Set(photos.map((photo) => photo.shotKey)), [photos]);
  const progress = getShotProgress(template, savedKeys);
  const uploadsInProgress = pending.some((item) => item.status !== "failed");

  const cameraHref = `/fahrzeuge/${vehicleId}/kamera`;
  const detailHref = `/fahrzeuge/${vehicleId}`;

  function handleRetake(slot: PhotoSlot) {
    setSelectedKey(null);
    if (!slot.required) {
      fileInputRef.current?.click();
      return;
    }
    router.push(`${cameraHref}?shot=${encodeURIComponent(slot.key)}&zurueck=fotos`);
  }

  async function handleDelete(slot: PhotoSlot) {
    if (!slot.photo) return;
    try {
      await deletePhotoAndSyncStatus(getAppServices().backend.data, template, slot.photo);
      toast.success("Foto gelöscht.");
      reload();
    } catch (error) {
      toast.error(toUserMessage(error));
      throw error;
    }
  }

  async function handleComplete() {
    setCompleting(true);
    try {
      await completeCapture(getAppServices().backend.data, template, vehicleId);
      toast.success("Aufnahmen abgeschlossen.");
      reload();
    } catch (error) {
      toast.error(toUserMessage(error));
    } finally {
      setCompleting(false);
    }
  }

  async function handleAddFiles(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []).filter((file) => file.type.startsWith("image/"));
    event.target.value = "";
    if (files.length === 0) return;
    const { uploads } = getAppServices();
    const usedKeys = new Set<string>([
      ...photos.map((photo) => photo.shotKey),
      ...pending.map((item) => item.shotKey),
    ]);
    for (const file of files) {
      const extra = getNextExtraShot(usedKeys);
      usedKeys.add(extra.key);
      await uploads.enqueue({
        vehicleId,
        shotKey: extra.key,
        shotOrder: extra.order,
        title: extra.title,
        file,
        takenAt: new Date(file.lastModified || Date.now()).toISOString(),
      });
    }
    toast.success(files.length === 1 ? "Zusatzfoto wird gespeichert…" : `${files.length} Zusatzfotos werden gespeichert…`);
  }

  if (state.status !== "ready") {
    return (
      <>
        <AppHeader title="Fotos überprüfen" backHref={detailHref} />
        <PageContainer>
          {state.status === "loading" && <PageLoading message="Fotos werden geladen…" />}
          {state.status === "error" && <ErrorState message={state.message} onRetry={reload} />}
          {state.status === "not_found" && <VehicleNotFound />}
        </PageContainer>
      </>
    );
  }

  const isCompleted = canProcessVehicle(state.vehicle.status);

  return (
    <>
      <AppHeader title="Fotos überprüfen" backHref={detailHref} />
      <PageContainer>
        <p className="text-sm text-ae-muted">{vehicleDisplayName(state.vehicle)}</p>
        <div className="mt-1 mb-2 flex items-baseline justify-between">
          <h2 className="text-xl font-bold tracking-tight tabular-nums">
            {progress.captured} / {progress.required} Fotos
          </h2>
          {progress.isComplete ? (
            <span className="flex items-center gap-1 text-sm font-semibold text-ae-success">
              <CircleCheck className="size-4" aria-hidden />
              Vollständig
            </span>
          ) : (
            <span className="text-sm text-ae-muted">Noch {progress.remaining} offen</span>
          )}
        </div>
        <ProgressBar
          value={progress.captured}
          max={progress.required}
          label="Fotofortschritt"
          tone={progress.isComplete ? "success" : "blue"}
          className="mb-2"
        />
        <p className="mb-5 text-xs text-ae-subtle">Tippen Sie auf ein Foto für die Großansicht.</p>

        <PhotoGrid slots={slots.required} onSelect={(slot) => setSelectedKey(slot.key)} label="Pflichtfotos" />

        {isCompleted && (
          <Button
            variant="ghost"
            size="sm"
            className="mt-4"
            onClick={() => fileInputRef.current?.click()}
            icon={<ImagePlus className="size-4" aria-hidden />}
          >
            Weitere Fotos hinzufügen
          </Button>
        )}

        {slots.extras.length > 0 && (
          <section className="mt-6" aria-labelledby="extras-title">
            <h3 id="extras-title" className="mb-3 text-sm font-semibold text-ae-muted">
              Zusatzfotos
            </h3>
            <PhotoGrid slots={slots.extras} onSelect={(slot) => setSelectedKey(slot.key)} label="Zusatzfotos" />
          </section>
        )}

        <StickyActions>
          <div className="flex flex-col gap-2 sm:flex-row-reverse sm:justify-start">
            {isCompleted ? (
              <>
                <ButtonLink
                  href={`/fahrzeuge/${vehicleId}/bearbeiten`}
                  size="lg"
                  icon={<Pencil className="size-5" aria-hidden />}
                >
                  Fotos bearbeiten
                </ButtonLink>
                <ButtonLink href={detailHref} variant="ghost" size="md">
                  Später bearbeiten
                </ButtonLink>
              </>
            ) : (
              <>
                {!progress.isComplete && (
                  <p className="text-center text-xs text-ae-muted sm:hidden">
                    Abschließen ist möglich, sobald alle {progress.required} Pflichtfotos gespeichert sind.
                  </p>
                )}
                <LoadingButton
                  size="lg"
                  onClick={handleComplete}
                  loading={completing}
                  loadingText="Wird abgeschlossen…"
                  disabled={!progress.isComplete || uploadsInProgress}
                  icon={<CircleCheck className="size-5" aria-hidden />}
                >
                  Aufnahmen abschließen
                </LoadingButton>
                <Button
                  size="md"
                  variant="secondary"
                  onClick={() => fileInputRef.current?.click()}
                  icon={<ImagePlus className="size-5" aria-hidden />}
                >
                  Weitere Fotos hinzufügen
                </Button>
              </>
            )}
          </div>
        </StickyActions>
      </PageContainer>

      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={handleAddFiles}
        aria-hidden
        tabIndex={-1}
      />

      <PhotoViewer
        slots={allSlots}
        selectedKey={selectedKey}
        onNavigate={setSelectedKey}
        onClose={() => setSelectedKey(null)}
        onRetake={handleRetake}
        onDelete={handleDelete}
        onRetryUpload={(slot) => slot.pending && getAppServices().uploads.retry(slot.pending.id)}
      />
    </>
  );
}
