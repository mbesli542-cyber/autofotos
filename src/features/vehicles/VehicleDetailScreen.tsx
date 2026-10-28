"use client";

import { Camera, CloudOff, ImageOff, Images, Pencil, TriangleAlert } from "lucide-react";
import { useRouter } from "next/navigation";
import { useLayoutEffect, useMemo, useState } from "react";
import { AppHeader } from "@/components/layout/AppHeader";
import { PageContainer } from "@/components/layout/PageContainer";
import { PhotoGrid } from "@/components/photos/PhotoGrid";
import { PhotoVersionToggle } from "@/components/photos/PhotoVersionToggle";
import { PhotoViewer } from "@/components/photos/PhotoViewer";
import { processedNeedsCheck, type PhotoVariant } from "@/components/photos/ShotThumbnail";
import { ButtonLink } from "@/components/ui/Button";
import { ErrorState } from "@/components/ui/ErrorState";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { PageLoading } from "@/components/ui/Spinner";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { useToast } from "@/components/ui/Toast";
import { VehicleInfoCard } from "@/components/vehicles/VehicleInfoCard";
import { usePendingUploads } from "@/hooks/use-upload-queue";
import { useVehicleDetail } from "@/hooks/use-vehicle-data";
import { getAppServices, getShotTemplate } from "@/lib/app-services";
import { vehicleDisplayName } from "@/lib/format";
import { buildPhotoSlots, type PhotoSlot } from "@/lib/photos/photo-slots";
import { getShotProgress } from "@/lib/shots/shot-progress";
import { canProcessVehicle } from "@/lib/vehicles/status";
import { deletePhotoAndSyncStatus } from "@/lib/workflow/vehicle-workflow";
import { useExtraPhotoRetake } from "@/features/review/use-extra-photo-retake";
import { VehicleNotFound } from "./VehicleNotFound";

export function VehicleDetailScreen({
  vehicleId,
  initialVariant = "original",
}: {
  vehicleId: string;
  initialVariant?: PhotoVariant;
}) {
  const router = useRouter();
  const toast = useToast();
  const template = getShotTemplate();
  const { state, reload } = useVehicleDetail(vehicleId);
  const pending = usePendingUploads(vehicleId);
  const extraRetake = useExtraPhotoRetake(vehicleId);
  // The link picks the version (?ansicht=bearbeitet, e.g. "Ergebnisse ansehen").
  // Routes stay mounted, so a choice made with the toggle only holds while the
  // page is shown: coming back follows the link again.
  const [chosenVariant, setChosenVariant] = useState<PhotoVariant | null>(null);
  const variant = chosenVariant ?? initialVariant;
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  useLayoutEffect(() => () => setChosenVariant(null), []);

  const photos = useMemo(() => (state.status === "ready" ? state.photos : []), [state]);
  const slots = useMemo(() => buildPhotoSlots(template, photos, pending), [template, photos, pending]);
  const allSlots = useMemo(() => [...slots.required, ...slots.extras], [slots]);
  const savedKeys = useMemo(() => new Set(photos.map((photo) => photo.shotKey)), [photos]);
  const progress = getShotProgress(template, savedKeys);
  const hasProcessed = photos.some((photo) => photo.urls.processed);

  const cameraHref = `/fahrzeuge/${vehicleId}/kamera`;
  const reviewHref = `/fahrzeuge/${vehicleId}/fotos`;
  const processHref = `/fahrzeuge/${vehicleId}/bearbeiten`;

  /** From the large view – it shows why a delete failed (a toast would be hidden behind it). */
  async function handleDelete(slot: PhotoSlot) {
    if (!slot.photo) return;
    await deletePhotoAndSyncStatus(getAppServices().backend.data, template, slot.photo);
    toast.success("Foto gelöscht.");
    reload();
  }

  function handleRetake(slot: PhotoSlot) {
    setSelectedKey(null);
    if (!slot.required) {
      // Not part of the guided camera: ONE picked file replaces exactly this photo.
      extraRetake.retake(slot);
      return;
    }
    router.push(`${cameraHref}?shot=${encodeURIComponent(slot.key)}`);
  }

  if (state.status !== "ready") {
    return (
      <>
        <AppHeader title="Fahrzeug" backHref="/fahrzeuge" />
        <PageContainer>
          {state.status === "loading" && <PageLoading message="Fahrzeug wird geladen…" />}
          {state.status === "error" && <ErrorState message={state.message} onRetry={reload} />}
          {state.status === "not_found" && <VehicleNotFound />}
        </PageContainer>
      </>
    );
  }

  const { vehicle } = state;
  const cover = photos[0];
  const coverUrl = cover ? (variant === "processed" ? cover.urls.processed : cover.urls.original) : null;
  const failed = pending.filter((item) => item.status === "failed");
  /** Failed uploads a retry can still save. */
  const failedUploads = failed.filter((item) => item.retryable).length;
  /** Files the server refused for good: only "Verwerfen" in the large view helps. */
  const refusedUploads = failed.length - failedUploads;
  /** Processed versions the processor asks to check ("Bearbeitet" view). */
  const toCheck = allSlots.filter((slot) => processedNeedsCheck(slot, variant)).length;

  return (
    <>
      <AppHeader title={vehicleDisplayName(vehicle)} backHref="/fahrzeuge" />
      <PageContainer className="flex flex-col gap-5">
        {/* Hero */}
        <section className="overflow-hidden rounded-2xl border border-ae-border bg-ae-surface">
          <div className="relative aspect-[16/9] bg-ae-surface-3 sm:aspect-[21/9]">
            {cover && coverUrl ? (
              <img
                src={coverUrl}
                alt={`${vehicleDisplayName(vehicle)} – ${cover.title}${variant === "processed" ? " (bearbeitet)" : ""}`}
                className="size-full object-cover"
              />
            ) : cover ? (
              // "Bearbeitet" never falls back to the original photo.
              <div className="flex size-full flex-col items-center justify-center gap-2 text-ae-subtle">
                <ImageOff className="size-9" aria-hidden />
                <span className="text-sm font-medium">Noch nicht bearbeitet</span>
              </div>
            ) : (
              <div className="flex size-full items-center justify-center text-ae-subtle">
                <Camera className="size-10" aria-hidden />
              </div>
            )}
            <StatusBadge status={vehicle.status} onImage className="absolute top-3 left-3" />
          </div>
          <div className="p-4">
            <h2 className="text-xl font-bold tracking-tight">{vehicleDisplayName(vehicle)}</h2>
            <p className="mt-0.5 text-sm text-ae-muted">
              {[vehicle.licensePlate, vehicle.internalReference, vehicle.color].filter(Boolean).join(" · ") ||
                "Keine weiteren Angaben"}
            </p>

            <div className="mt-4">
              <div className="mb-1.5 flex items-baseline justify-between text-sm">
                <span className="font-semibold">
                  {progress.captured} / {progress.required} Fotos
                </span>
                <span className="text-ae-muted">
                  {progress.isComplete ? "Alle Pflichtfotos vorhanden" : `Noch ${progress.remaining} Fotos`}
                </span>
              </div>
              <ProgressBar
                value={progress.captured}
                max={progress.required}
                label="Fotofortschritt"
                tone={progress.isComplete ? "success" : "blue"}
              />
            </div>

            <div className="mt-4 grid gap-2 sm:flex">
              {!progress.isComplete && (
                <ButtonLink href={cameraHref} size="lg" icon={<Camera className="size-5" aria-hidden />}>
                  {progress.captured === 0 ? "Aufnahmen starten" : "Aufnahmen fortsetzen"}
                </ButtonLink>
              )}
              {progress.isComplete && canProcessVehicle(vehicle.status) && (
                <ButtonLink href={processHref} size="lg" icon={<Pencil className="size-5" aria-hidden />}>
                  Fotos bearbeiten
                </ButtonLink>
              )}
              {progress.isComplete && !canProcessVehicle(vehicle.status) && (
                <ButtonLink href={reviewHref} size="lg" icon={<Images className="size-5" aria-hidden />}>
                  Fotos überprüfen & abschließen
                </ButtonLink>
              )}
              {(progress.captured > 0 && (!progress.isComplete || canProcessVehicle(vehicle.status))) && (
                <ButtonLink href={reviewHref} size="lg" variant="secondary" icon={<Images className="size-5" aria-hidden />}>
                  Fotos überprüfen
                </ButtonLink>
              )}
            </div>
          </div>
        </section>

        {pending.length > 0 && (
          <div
            className="flex items-center gap-3 rounded-xl border border-ae-warning/30 bg-ae-warning/8 px-4 py-3 text-sm"
            role="status"
          >
            <CloudOff className="size-5 shrink-0 text-ae-warning" aria-hidden />
            <div className="flex flex-1 flex-col gap-1 text-ae-muted">
              {failedUploads > 0 && (
                <p>{`${failedUploads} Foto(s) konnten noch nicht gespeichert werden. Sie bleiben auf diesem Gerät.`}</p>
              )}
              {refusedUploads > 0 && (
                <p>
                  {refusedUploads === 1
                    ? "1 Foto kann nicht gespeichert werden. Bitte das Foto antippen und in der Großansicht „Verwerfen“ wählen."
                    : `${refusedUploads} Fotos können nicht gespeichert werden. Bitte die Fotos antippen und in der Großansicht „Verwerfen“ wählen.`}
                </p>
              )}
              {failed.length === 0 && <p>{`${pending.length} Foto(s) werden gespeichert…`}</p>}
            </div>
            {failedUploads > 0 && (
              <button
                type="button"
                className="font-semibold text-ae-warning"
                onClick={() => getAppServices().uploads.retryAll()}
              >
                Erneut versuchen
              </button>
            )}
          </div>
        )}

        {/* Photos */}
        <section aria-labelledby="photos-title">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 id="photos-title" className="font-semibold">
              Aufnahmen
            </h2>
            {(hasProcessed || variant === "processed") && (
              <PhotoVersionToggle value={variant} onChange={setChosenVariant} />
            )}
          </div>
          {toCheck > 0 && (
            <p className="mb-3 flex items-start gap-1.5 text-sm text-ae-warning">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
              {toCheck === 1
                ? "Bitte ein bearbeitetes Foto prüfen – Hinweis in der Großansicht."
                : `Bitte ${toCheck} bearbeitete Fotos prüfen – Hinweise in der Großansicht.`}
            </p>
          )}
          <PhotoGrid slots={slots.required} variant={variant} onSelect={(slot) => setSelectedKey(slot.key)} label="Pflichtfotos" />
          {slots.extras.length > 0 && (
            <>
              <h3 className="mt-5 mb-3 text-sm font-semibold text-ae-muted">Zusatzfotos</h3>
              <PhotoGrid slots={slots.extras} variant={variant} onSelect={(slot) => setSelectedKey(slot.key)} label="Zusatzfotos" />
            </>
          )}
        </section>

        <VehicleInfoCard vehicle={vehicle} editHref={`/fahrzeuge/${vehicleId}/daten`} />
      </PageContainer>

      {extraRetake.input}

      <PhotoViewer
        // Re-created when the grid switches version, so the viewer opens in that version.
        key={variant}
        slots={allSlots}
        selectedKey={selectedKey}
        defaultVariant={variant}
        onNavigate={setSelectedKey}
        onClose={() => setSelectedKey(null)}
        onRetake={handleRetake}
        onDelete={handleDelete}
        onRetryUpload={(slot) => slot.pending && getAppServices().uploads.retry(slot.pending.id)}
      />
    </>
  );
}
