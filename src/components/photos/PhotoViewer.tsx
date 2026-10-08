"use client";

import { Camera, ChevronLeft, ChevronRight, ImageOff, RefreshCw, RotateCcw, Trash2, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { Button } from "@/components/ui/Button";
import { LoadingButton } from "@/components/ui/LoadingButton";
import { Spinner } from "@/components/ui/Spinner";
import { getShotTemplate } from "@/lib/app-services";
import { formatDateTime } from "@/lib/format";
import { formatSlotNumber, type PhotoSlot } from "@/lib/photos/photo-slots";
import { getShotTreatment } from "@/lib/processing/shot-treatment";
import { PhotoVersionToggle } from "./PhotoVersionToggle";
import type { PhotoVariant } from "./ShotThumbnail";

/**
 * Large preview with Original/Bearbeitet switch and the actions
 * "Foto wiederholen" and "Foto löschen".
 */
export function PhotoViewer({
  slots,
  selectedKey,
  defaultVariant = "original",
  onNavigate,
  onClose,
  onRetake,
  onDelete,
  onRetryUpload,
}: {
  slots: readonly PhotoSlot[];
  selectedKey: string | null;
  defaultVariant?: PhotoVariant;
  onNavigate: (key: string) => void;
  onClose: () => void;
  onRetake: (slot: PhotoSlot) => void;
  onDelete?: (slot: PhotoSlot) => Promise<void>;
  onRetryUpload?: (slot: PhotoSlot) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [variant, setVariant] = useState<PhotoVariant>(defaultVariant);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [imageLoaded, setImageLoaded] = useState<string | null>(null);

  const index = slots.findIndex((slot) => slot.key === selectedKey);
  const slot = index >= 0 ? slots[index] : undefined;
  const open = Boolean(slot);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  // Close when the route gets hidden (Next.js keeps routes mounted).
  useLayoutEffect(() => () => dialogRef.current?.close(), []);

  if (!slot) {
    return <dialog ref={dialogRef} className="hidden" aria-hidden />;
  }

  const processedUrl = slot.pending ? null : (slot.photo?.urls.processed ?? null);
  // "Bearbeitet" only ever shows a real processed version – never the original as a stand-in.
  const shownVariant: PhotoVariant = variant;
  const showToggle = Boolean(slot.photo) && (processedUrl !== null || variant === "processed");
  const treatment = getShotTreatment(getShotTemplate(), slot.key);
  const imageUrl =
    shownVariant === "processed"
      ? processedUrl
      : (slot.pending?.thumbnailUrl ?? slot.photo?.urls.original ?? null);
  const previous = slots[index - 1];
  const next = slots[index + 1];

  function go(target: PhotoSlot | undefined) {
    if (!target) return;
    setConfirmingDelete(false);
    onNavigate(target.key);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key === "ArrowLeft") go(previous);
    if (event.key === "ArrowRight") go(next);
  }

  async function handleDelete() {
    if (!onDelete || !slot) return;
    setDeleting(true);
    try {
      await onDelete(slot);
      setConfirmingDelete(false);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <dialog
      ref={dialogRef}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onKeyDown={handleKeyDown}
      aria-labelledby="photo-viewer-title"
      className="m-0 h-dvh max-h-none w-screen max-w-none bg-black p-0 text-ae-text sm:m-auto sm:h-auto sm:max-h-[94dvh] sm:w-[min(96vw,1100px)] sm:rounded-2xl sm:border sm:border-ae-border"
    >
      <div className="flex h-full flex-col sm:h-auto sm:max-h-[94dvh]">
        <header className="pt-safe flex items-center gap-3 border-b border-ae-border/70 bg-ae-bg px-3 py-2">
          <div className="min-w-0 flex-1 pl-1">
            <p className="text-xs font-semibold text-ae-muted tabular-nums">
              {slot.required ? `${formatSlotNumber(slot)} / ${String(slots.filter((s) => s.required).length).padStart(2, "0")}` : "Zusatzfoto"}
            </p>
            <h2 id="photo-viewer-title" className="truncate font-semibold">
              {slot.title}
            </h2>
          </div>
          {showToggle && (
            <div className="hidden sm:block">
              <PhotoVersionToggle value={shownVariant} onChange={setVariant} />
            </div>
          )}
          <button
            type="button"
            onClick={onClose}
            className="flex size-10 items-center justify-center rounded-xl hover:bg-ae-surface-2"
            aria-label="Großansicht schließen"
          >
            <X className="size-5" aria-hidden />
          </button>
        </header>

        <div className="relative flex min-h-0 flex-1 items-center justify-center bg-black sm:min-h-[50vh]">
          {imageUrl ? (
            <>
              {imageLoaded !== imageUrl && (
                <Spinner className="absolute size-7 text-ae-muted" label="Foto wird geladen…" />
              )}
              <img
                key={imageUrl}
                src={imageUrl}
                alt={`${slot.title} – ${shownVariant === "processed" ? "Bearbeitet" : "Originalfoto"}`}
                onLoad={() => setImageLoaded(imageUrl)}
                className="max-h-full w-full object-contain sm:max-h-[68dvh]"
              />
            </>
          ) : shownVariant === "processed" && (slot.photo || slot.pending) ? (
            <div className="flex flex-col items-center gap-3 p-8 text-center text-ae-muted">
              <ImageOff className="size-10" aria-hidden />
              <p className="font-semibold text-ae-text">Noch nicht bearbeitet</p>
              <p className="max-w-xs text-sm">
                Für dieses Foto gibt es noch keine bearbeitete Version. Das Original finden Sie unter „Original“.
              </p>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-3 p-8 text-center text-ae-muted">
              <Camera className="size-10" aria-hidden />
              <p>Für diese Aufnahme gibt es noch kein Foto.</p>
            </div>
          )}

          {previous && (
            <button
              type="button"
              onClick={() => go(previous)}
              className="absolute top-1/2 left-2 flex size-11 -translate-y-1/2 items-center justify-center rounded-full bg-black/60 text-white backdrop-blur hover:bg-black/80"
              aria-label={`Vorheriges Foto: ${previous.title}`}
            >
              <ChevronLeft className="size-6" aria-hidden />
            </button>
          )}
          {next && (
            <button
              type="button"
              onClick={() => go(next)}
              className="absolute top-1/2 right-2 flex size-11 -translate-y-1/2 items-center justify-center rounded-full bg-black/60 text-white backdrop-blur hover:bg-black/80"
              aria-label={`Nächstes Foto: ${next.title}`}
            >
              <ChevronRight className="size-6" aria-hidden />
            </button>
          )}
          {shownVariant === "processed" && imageUrl && (
            <span className="absolute top-3 left-3 rounded-md bg-ae-blue px-2 py-0.5 text-xs font-semibold text-white">
              {treatment === "original_environment" ? "Bearbeitet · Originalumgebung" : "Bearbeitet"}
            </span>
          )}
        </div>

        <footer className="pb-safe border-t border-ae-border/70 bg-ae-bg px-4 pt-3">
          {showToggle && (
            <div className="mb-3 flex justify-center sm:hidden">
              <PhotoVersionToggle value={shownVariant} onChange={setVariant} />
            </div>
          )}
          {slot.photo && !slot.pending && (
            <p className="mb-3 text-center text-xs text-ae-subtle">
              Aufgenommen am {formatDateTime(slot.photo.takenAt)}
              {slot.photo.width && slot.photo.height
                ? ` · ${slot.photo.width} × ${slot.photo.height} px`
                : ""}
            </p>
          )}
          {slot.pending && (
            <p className="mb-3 text-center text-xs text-ae-warning">
              {slot.pending.status === "failed"
                ? (slot.pending.error ?? "Foto konnte nicht gespeichert werden.")
                : "Foto wird gespeichert…"}
            </p>
          )}

          {confirmingDelete ? (
            <div className="mb-3 rounded-xl border border-ae-danger/30 bg-ae-danger/8 p-3">
              <p className="text-sm font-semibold">Foto wirklich löschen?</p>
              <p className="mt-1 text-xs text-ae-muted">
                Das Foto wird aus der Aufnahmeserie entfernt. Die Originaldatei bleibt archiviert.
              </p>
              <div className="mt-3 grid grid-cols-2 gap-2">
                <Button variant="secondary" onClick={() => setConfirmingDelete(false)} disabled={deleting}>
                  Abbrechen
                </Button>
                <LoadingButton variant="danger" onClick={handleDelete} loading={deleting} loadingText="Wird gelöscht…">
                  Löschen
                </LoadingButton>
              </div>
            </div>
          ) : (
            <div className="mb-3 grid grid-cols-2 gap-2 [&>button]:px-2 [&>button]:text-sm [&>button]:whitespace-nowrap">
              {slot.pending?.status === "failed" && onRetryUpload ? (
                <Button
                  variant="secondary"
                  onClick={() => onRetryUpload(slot)}
                  icon={<RefreshCw className="size-4" aria-hidden />}
                >
                  Erneut speichern
                </Button>
              ) : (
                <Button
                  variant="danger"
                  onClick={() => setConfirmingDelete(true)}
                  disabled={!slot.photo || Boolean(slot.pending) || !onDelete}
                  icon={<Trash2 className="size-4" aria-hidden />}
                >
                  Foto löschen
                </Button>
              )}
              <Button
                variant="primary"
                onClick={() => onRetake(slot)}
                icon={
                  slot.photo || slot.pending ? (
                    <RotateCcw className="size-4" aria-hidden />
                  ) : (
                    <Camera className="size-4" aria-hidden />
                  )
                }
              >
                {slot.photo || slot.pending ? "Foto wiederholen" : "Foto aufnehmen"}
              </Button>
            </div>
          )}
        </footer>
      </div>
    </dialog>
  );
}
