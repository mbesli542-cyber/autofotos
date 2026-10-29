"use client";

import { Camera, ChevronLeft, ChevronRight, ImageOff, RefreshCw, RotateCcw, Trash2, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { ProcessingWarningList } from "@/components/processing/ProcessingWarningList";
import { Button } from "@/components/ui/Button";
import { LoadingButton } from "@/components/ui/LoadingButton";
import { Spinner } from "@/components/ui/Spinner";
import { getAppServices, getShotTemplate } from "@/lib/app-services";
import { AppError, toUserMessage } from "@/lib/errors";
import { formatDateTime } from "@/lib/format";
import { formatSlotNumber, type PhotoSlot } from "@/lib/photos/photo-slots";
import { needsResultCheck } from "@/lib/processing/processing-warnings";
import { getShotTreatment } from "@/lib/processing/shot-treatment";
import { PhotoVersionToggle } from "./PhotoVersionToggle";
import type { PhotoVariant } from "./ShotThumbnail";

/**
 * A question of the viewer – always for one photo or one capture. `error`:
 * why the last answer failed, shown in the question itself (a toast would be
 * hidden behind the modal viewer).
 */
type Question =
  | { kind: "delete"; photoId: string; error?: string }
  | { kind: "discard"; uploadId: string; error?: string };

/** What a question is about: the photo to delete or the capture to discard. */
function targetOf(question: Question): string {
  return question.kind === "delete" ? `photo:${question.photoId}` : `upload:${question.uploadId}`;
}

const DISCARD_FAILED_MESSAGE = "Das Foto konnte nicht verworfen werden. Bitte versuchen Sie es erneut.";

/**
 * Large preview with Original/Bearbeitet switch and the actions
 * "Foto wiederholen" and "Foto löschen" – for a capture that could not be
 * saved "Erneut speichern" and "Verwerfen". "Bearbeitet" shows the
 * processor's hints on the result (e.g. "bitte das Ergebnis prüfen").
 * `onDelete` rejects when the photo could not be deleted – the viewer says
 * why. A delete or discard on its way belongs to its photo or capture: only
 * its own question shows it busy, and only that question ends with its
 * answer. The actions come right below the version switch, before hints and
 * details, so they never need scrolling – however many hints a small phone
 * shows. Short viewports (phones in landscape) show details and actions next
 * to the photo, so hints never squeeze it.
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
  const [question, setQuestion] = useState<Question | null>(null);
  const [questionFor, setQuestionFor] = useState(selectedKey);
  /** Targets (targetOf) whose answer – a delete or discard – is on its way. */
  const [answering, setAnswering] = useState<ReadonlySet<string>>(() => new Set());
  const [imageLoaded, setImageLoaded] = useState<string | null>(null);

  // A question belongs to the photo it was asked for: showing another photo
  // or closing the viewer dismisses it (adjusted during render, no effect).
  if (questionFor !== selectedKey) {
    setQuestionFor(selectedKey);
    setQuestion(null);
  }

  const index = slots.findIndex((slot) => slot.key === selectedKey);
  const slot = index >= 0 ? slots[index] : undefined;
  const open = Boolean(slot);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  // Close when the route gets hidden (Next.js keeps routes mounted); an
  // unanswered question ends with it – the shot may have another photo or
  // capture when the route is shown again.
  useLayoutEffect(
    () => () => {
      dialogRef.current?.close();
      setQuestion(null);
    },
    [],
  );

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
  /** A capture of this shot that could not be saved (still on this device). */
  const failedUpload = slot.pending?.status === "failed" ? slot.pending : null;
  const canDelete = Boolean(slot.photo) && !slot.pending && Boolean(onDelete);
  // Shown only while the shot still has the photo / capture it was asked for.
  const askingDelete = question?.kind === "delete" && canDelete && question.photoId === slot.photo?.id;
  const askingDiscard = question?.kind === "discard" && question.uploadId === failedUpload?.id;
  /** The answer to the question shown is on its way (also when asked again meanwhile). */
  const busy = question !== null && answering.has(targetOf(question));
  /** The processor's hints on the processed version shown. */
  const warnings = shownVariant === "processed" && processedUrl ? (slot.photo?.processingWarnings ?? []) : [];

  function go(target: PhotoSlot | undefined) {
    if (!target) return;
    onNavigate(target.key);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key === "ArrowLeft") go(previous);
    if (event.key === "ArrowRight") go(next);
  }

  /**
   * Answers a question with `action`. Busy, success and failure belong to the
   * photo or capture it asked about: a question asked meanwhile for another
   * one is neither shown busy nor ended by it, nor given its error. Success
   * ends the question; on failure it stays (nothing changed) and says why.
   */
  async function answer(asked: Question, action: () => Promise<void>, failure: (error: unknown) => string) {
    const target = targetOf(asked);
    const isAsked = (current: Question | null): current is Question => current !== null && targetOf(current) === target;
    setAnswering((current) => new Set(current).add(target));
    try {
      await action();
      setQuestion((current) => (isAsked(current) ? null : current));
    } catch (error) {
      const message = failure(error);
      setQuestion((current) => (isAsked(current) ? { ...current, error: message } : current));
    } finally {
      setAnswering((current) => {
        const next = new Set(current);
        next.delete(target);
        return next;
      });
    }
  }

  async function handleDelete() {
    if (!onDelete || !slot || question?.kind !== "delete" || !askingDelete || busy) return;
    const shown = slot;
    await answer(question, () => onDelete(shown), (error) => toUserMessage(error));
  }

  async function handleDiscard() {
    if (question?.kind !== "discard" || !askingDiscard || busy) return;
    const { uploadId } = question;
    await answer(
      question,
      () => getAppServices().uploads.discard(uploadId),
      // The queue's own reason (e.g. the capture is being saved right now); never a storage error.
      (error) => (error instanceof AppError ? toUserMessage(error) : DISCARD_FAILED_MESSAGE),
    );
  }

  function askDelete() {
    if (slot?.photo) setQuestion({ kind: "delete", photoId: slot.photo.id });
  }

  function askDiscard() {
    if (failedUpload) setQuestion({ kind: "discard", uploadId: failedUpload.id });
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
      className="m-0 h-dvh max-h-none w-screen max-w-none bg-black p-0 text-ae-text sm:m-auto sm:h-[min(94dvh,66rem)] sm:w-[min(96vw,1100px)] sm:rounded-2xl sm:border sm:border-ae-border short:m-0 short:h-dvh short:w-screen short:rounded-none short:border-0"
    >
      <div className="flex h-full flex-col">
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

        {/*
          The photo keeps at least 40 % of the height; details, hints and actions
          below it scroll if they need more. Short viewports (phones in landscape)
          put them next to the photo instead.
        */}
        <div className="flex min-h-0 flex-1 flex-col short:flex-row">
          <div className="relative flex min-h-[40%] flex-1 items-center justify-center bg-black short:min-h-0 short:min-w-0">
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
                  className="max-h-full w-full object-contain"
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
                {needsResultCheck(warnings) ? " · bitte prüfen" : ""}
              </span>
            )}
          </div>

          {/*
            The actions (or the question) right below the version switch, hints and
            details after them – in every layout: the actions never need scrolling,
            however many hints a small phone shows.
          */}
          <footer className="pb-safe min-h-0 overflow-y-auto border-t border-ae-border/70 bg-ae-bg px-4 pt-3 short:flex short:w-72 short:shrink-0 short:flex-col short:border-t-0 short:border-l">
            {showToggle && (
              <div className="mb-3 flex justify-center sm:hidden">
                <PhotoVersionToggle value={shownVariant} onChange={setVariant} />
              </div>
            )}

            {askingDelete ? (
              <div className="mb-3 rounded-xl border border-ae-danger/30 bg-ae-danger/8 p-3">
                <p className="text-sm font-semibold">Foto wirklich löschen?</p>
                <p className="mt-1 text-xs text-ae-muted">
                  Das Foto wird aus der Aufnahmeserie entfernt. Die Originaldatei bleibt archiviert.
                </p>
                {question.error && (
                  <p role="alert" className="mt-2 text-xs font-medium text-ae-danger">
                    {question.error}
                  </p>
                )}
                <div className="mt-3 grid grid-cols-2 gap-2 short:grid-cols-1">
                  <Button variant="secondary" onClick={() => setQuestion(null)} disabled={busy}>
                    Abbrechen
                  </Button>
                  <LoadingButton variant="danger" onClick={handleDelete} loading={busy} loadingText="Wird gelöscht…">
                    Löschen
                  </LoadingButton>
                </div>
              </div>
            ) : askingDiscard ? (
              <div className="mb-3 rounded-xl border border-ae-danger/30 bg-ae-danger/8 p-3">
                <p className="text-sm font-semibold">Foto verwerfen?</p>
                <p className="mt-1 text-xs text-ae-muted">
                  Das nicht gespeicherte Foto wird von diesem Gerät gelöscht.
                  {slot.photo ? " Das zuletzt gespeicherte Foto bleibt erhalten." : ""}
                </p>
                {question.error && (
                  <p role="alert" className="mt-2 text-xs font-medium text-ae-danger">
                    {question.error}
                  </p>
                )}
                <div className="mt-3 grid grid-cols-2 gap-2 short:grid-cols-1">
                  <Button variant="secondary" onClick={() => setQuestion(null)} disabled={busy}>
                    Abbrechen
                  </Button>
                  <LoadingButton variant="danger" onClick={handleDiscard} loading={busy} loadingText="Wird verworfen…">
                    Verwerfen
                  </LoadingButton>
                </div>
              </div>
            ) : (
              <div className="mb-3 grid grid-cols-2 gap-2 short:grid-cols-1 [&>button]:px-2 [&>button]:text-sm [&>button]:whitespace-nowrap">
                {failedUpload?.retryable && onRetryUpload ? (
                  <Button
                    variant="secondary"
                    onClick={() => onRetryUpload(slot)}
                    icon={<RefreshCw className="size-4" aria-hidden />}
                  >
                    Erneut speichern
                  </Button>
                ) : failedUpload ? (
                  // Not retryable here, e.g. a file type the server refuses for good.
                  <Button variant="danger" onClick={askDiscard} icon={<Trash2 className="size-4" aria-hidden />}>
                    Verwerfen
                  </Button>
                ) : (
                  <Button
                    variant="danger"
                    onClick={askDelete}
                    disabled={!canDelete}
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
                {failedUpload?.retryable && onRetryUpload && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="col-span-2 short:col-span-1"
                    onClick={askDiscard}
                    icon={<Trash2 className="size-4" aria-hidden />}
                  >
                    Verwerfen
                  </Button>
                )}
              </div>
            )}

            <ProcessingWarningList warnings={warnings} className="mx-auto mb-3 max-w-xl" />
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
          </footer>
        </div>
      </div>
    </dialog>
  );
}
