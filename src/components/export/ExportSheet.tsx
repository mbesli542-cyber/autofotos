"use client";

import { CircleCheck, Download, Share, TriangleAlert, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { Button } from "@/components/ui/Button";
import { LoadingButton } from "@/components/ui/LoadingButton";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { useSharePlatform, useTouchDevice } from "@/hooks/use-touch-device";
import { getAppServices } from "@/lib/app-services";
import { cn } from "@/lib/cn";
import { toUserMessage } from "@/lib/errors";
import {
  canShareFiles,
  canShareFileTypes,
  downloadBlob,
  ShareFailedError,
  shareFiles,
  shareTargetHint,
  splitIntoShareBatches,
} from "@/lib/export/device-save";
import {
  buildExportZip,
  ExportFileLoader,
  exportFileKey,
  exportMimeType,
  type ExportFileError,
} from "@/lib/export/export-files";
import {
  describeExportPlan,
  photoCount,
  reviewRows,
  selectExportItems,
  type ExportItem,
  type ExportPlan,
  type ExportVehicle,
} from "@/lib/export/listing-export";
import { ZipError } from "@/lib/export/zip";
import { buildExportArchiveName } from "@/lib/naming/file-naming";

/**
 * - idle:   the summary, waiting for "Speichern"
 * - busy:   the share sheet is open / the ZIP is being built
 * - shared: `next` parts of a share in several parts are still to come
 * - done:   handed over (`message`)
 * - failed: the share or the ZIP failed (`message`; `offerZip`: the ZIP is the way out)
 */
type Phase =
  | { kind: "idle" }
  | { kind: "busy"; text: string }
  | { kind: "shared"; next: number; total: number; selection: string }
  | { kind: "done"; title: string; message: string }
  | { kind: "failed"; message: string; offerZip: boolean };

const IDLE: Phase = { kind: "idle" };
const NONE: ReadonlySet<string> = new Set();

const STATUS_TONE: Record<ExportItem["status"], string> = {
  processed: "text-ae-warning",
  original: "text-ae-warning",
  retake: "text-ae-danger",
  missing: "text-ae-danger",
  unsaved: "text-ae-warning",
  extra: "text-ae-muted",
};

function zipErrorMessage(error: unknown): string {
  if (error instanceof ZipError && (error.code === "too_large" || error.code === "too_many_entries")) {
    return "Die Fotos sind zusammen zu groß für eine ZIP-Datei (höchstens 4 GB). Bitte die Fotos einzeln speichern.";
  }
  return toUserMessage(error, "Die ZIP-Datei konnte nicht erstellt werden. Bitte versuchen Sie es erneut.");
}

/** "09 Cockpit". */
function shotName(item: Pick<ExportItem, "order" | "title">): string {
  return `${String(item.order).padStart(2, "0")} ${item.title}`;
}

/** "AE_…_01_….jpg" or "AE_…_01_….jpg bis AE_…_15_….jpg". */
function fileRange(files: readonly File[]): string {
  const first = files[0]?.name ?? "";
  const last = files.at(-1)?.name ?? "";
  return files.length > 1 ? `${first} bis ${last}` : first;
}

function withKey(set: ReadonlySet<string>, key: string): ReadonlySet<string> {
  return new Set([...set, key]);
}

/**
 * "Alle Fotos speichern": a short summary of the listing set (what is
 * processed, what is still the original, what must be retaken), then the
 * files in shot order named AE_{ref}_{nn}_{shot}: the share sheet on phones
 * whose browser can share them, a ZIP download on desktops and as the
 * fallback – the screen says which before the tap. Rejected shots are only
 * saved when the employee ticks them. The files that will be saved are
 * loaded as soon as the sheet opens, so the share sheet opens straight from
 * the tap; a file that is gone can be left out (or replaced by the original)
 * instead of blocking the others.
 */
export function ExportSheet({
  open,
  vehicle,
  plan,
  onClose,
  onShowShot,
}: {
  open: boolean;
  vehicle: ExportVehicle;
  plan: ExportPlan;
  onClose: () => void;
  /** Opens a shot in the large view (from the list of shots to look at). */
  onShowShot?: (key: string) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const touch = useTouchDevice();
  const platform = useSharePlatform();
  const [loader] = useState(() => new ExportFileLoader(() => getAppServices().backend.data));
  const snapshot = useSyncExternalStore(loader.subscribe, loader.getSnapshot, loader.getSnapshot);
  const [includeRetakes, setIncludeRetakes] = useState(false);
  /** Shots left out because their file could not be loaded. */
  const [leaveOut, setLeaveOut] = useState<ReadonlySet<string>>(NONE);
  /** Shots whose processed file is gone: the original instead. */
  const [useOriginal, setUseOriginal] = useState<ReadonlySet<string>>(NONE);
  const [phase, setPhase] = useState<Phase>(IDLE);
  const [openedBefore, setOpenedBefore] = useState(open);

  // Every opening starts with the summary (adjusted during render, no effect).
  if (open !== openedBefore) {
    setOpenedBefore(open);
    if (open) {
      setIncludeRetakes(false);
      setLeaveOut(NONE);
      setUseOriginal(NONE);
      setPhase(IDLE);
    }
  }

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const selected = selectExportItems(plan, { includeRetakes, leaveOut, useOriginal });

  // Load the files that will be saved in the background while the summary is
  // read (rejected shots only once they are ticked).
  useEffect(() => {
    if (open) loader.want(selected);
  }, [open, selected, loader]);

  // Routes stay mounted: close and free the files when the page gets hidden.
  useLayoutEffect(
    () => () => {
      dialogRef.current?.close();
      loader.clear();
    },
    [loader],
  );

  const files = selected.flatMap((item) => {
    const file = snapshot.files.get(exportFileKey(item));
    return file ? [file] : [];
  });
  const failures = selected.flatMap((item) => {
    const error = snapshot.errors.get(exportFileKey(item));
    return error ? [{ item, error }] : [];
  });
  /** Files that are gone: leave them out or use the original. */
  const missing = failures.filter((failure) => failure.error.missing);
  /** Files that could not be loaded right now: try again. */
  const transient = failures.filter((failure) => !failure.error.missing);
  const ready = selected.length > 0 && files.length === selected.length;
  const selection = selected.map(exportFileKey).join("|");
  const batches = splitIntoShareBatches(files);
  const progress = phase.kind === "shared" && phase.selection === selection ? phase : null;
  const rows = reviewRows(plan);
  const archiveName = buildExportArchiveName({ internalReference: vehicle.internalReference, vehicleId: vehicle.id });
  const exampleName = selected[0]?.fileName ?? null;
  const busy = phase.kind === "busy";
  const finished = phase.kind === "done";
  /** Sharing failed on this phone: the ZIP is the way out. */
  const zipInstead = phase.kind === "failed" && phase.offerZip;
  // Whether this browser can share the files – known before they are loaded
  // (their names and types), confirmed with the loaded files.
  const canShare =
    touch &&
    selected.length > 0 &&
    (ready
      ? batches.every((batch) => canShareFiles(batch))
      : canShareFileTypes(selected.map((item) => ({ name: item.fileName ?? "", type: exportMimeType(item) }))));
  const method: "share" | "zip" = canShare && !zipInstead ? "share" : "zip";
  const leftOut = plan.items.filter((item) => leaveOut.has(item.key));
  const originals = selected.filter((item) => item.originalInstead);

  function close() {
    if (busy) return;
    loader.clear();
    onClose();
  }

  async function saveZip(list: File[]) {
    setPhase({ kind: "busy", text: "ZIP-Datei wird erstellt…" });
    try {
      downloadBlob(await buildExportZip(list, loader.crcOf), archiveName);
      setPhase({
        kind: "done",
        title: "ZIP-Datei wird heruntergeladen",
        message: `${archiveName} – ${photoCount(list.length)} in Aufnahme-Reihenfolge.`,
      });
    } catch (error) {
      setPhase({ kind: "failed", message: zipErrorMessage(error), offerZip: false });
    }
  }

  /** Straight from the tap: the share sheet needs a fresh user activation. */
  async function share(batch: File[], index: number) {
    setPhase({ kind: "busy", text: "Teilen-Menü ist geöffnet…" });
    try {
      const outcome = await shareFiles(batch);
      if (outcome === "cancelled") {
        // Closed by the employee: no error – back to where it was.
        setPhase(index > 0 ? { kind: "shared", next: index, total: batches.length, selection } : IDLE);
        return;
      }
      if (index + 1 < batches.length) {
        setPhase({ kind: "shared", next: index + 1, total: batches.length, selection });
        return;
      }
      // A finished share only means a target took the files – where they are now is up to it.
      setPhase({
        kind: "done",
        title: `${photoCount(files.length)} an das Teilen-Menü übergeben`,
        message: `${fileRange(files)} – in Aufnahme-Reihenfolge.`,
      });
    } catch (error) {
      setPhase({
        kind: "failed",
        message: error instanceof ShareFailedError ? error.message : toUserMessage(error),
        offerZip: true,
      });
    }
  }

  function handleSave() {
    if (!ready || busy) return;
    if (method === "share") {
      const index = progress?.next ?? 0;
      const batch = batches[index];
      if (batch) {
        void share(batch, index);
        return;
      }
    }
    void saveZip(files);
  }

  const saveLabel = progress
    ? `Teil ${progress.next + 1} von ${progress.total} sichern`
    : zipInstead
      ? "Als ZIP-Datei herunterladen"
      : !plan.complete
        ? "Trotzdem speichern"
        : method === "share"
          ? `${photoCount(selected.length)} speichern`
          : touch
            ? "Als ZIP-Datei herunterladen"
            : "ZIP herunterladen";

  return (
    <dialog
      ref={dialogRef}
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
      aria-labelledby="export-title"
      className="m-0 mt-auto max-h-[92dvh] w-full max-w-none rounded-t-2xl border border-b-0 border-ae-border bg-ae-surface p-0 text-ae-text shadow-[var(--shadow-card)] sm:m-auto sm:w-[min(92vw,520px)] sm:rounded-2xl sm:border-b short:max-h-dvh short:rounded-none"
    >
      {open && (
        <div className="flex max-h-[inherit] flex-col">
          <header className="flex items-start gap-3 px-5 pt-5 pb-3 short:pt-3 short:pb-2">
            <div className="min-w-0 flex-1">
              <h2 id="export-title" className="text-sm font-semibold text-ae-muted">
                Alle Fotos speichern
              </h2>
              <p className="mt-1 flex items-start gap-2 text-lg leading-snug font-bold" data-testid="export-summary">
                {plan.complete ? (
                  <CircleCheck className="mt-0.5 size-5 shrink-0 text-ae-success" aria-hidden />
                ) : (
                  <TriangleAlert className="mt-0.5 size-5 shrink-0 text-ae-warning" aria-hidden />
                )}
                <span>{describeExportPlan(plan)}</span>
              </p>
            </div>
            <button
              type="button"
              onClick={close}
              disabled={busy}
              className="-mt-1 -mr-2 flex size-10 shrink-0 items-center justify-center rounded-xl hover:bg-ae-surface-2 disabled:opacity-40"
              aria-label="Schließen"
            >
              <X className="size-5" aria-hidden />
            </button>
          </header>

          <div className="min-h-0 flex-1 overflow-y-auto px-5">
            {phase.kind === "done" ? (
              <div className="mb-4 flex items-start gap-3 rounded-xl border border-ae-success/30 bg-ae-success/10 p-3" role="status">
                <CircleCheck className="mt-0.5 size-5 shrink-0 text-ae-success" aria-hidden />
                <div className="min-w-0">
                  <p className="font-semibold">{phase.title}</p>
                  <p className="mt-0.5 text-sm [overflow-wrap:anywhere] text-ae-muted">{phase.message}</p>
                </div>
              </div>
            ) : (
              <div className="mb-3 text-sm text-ae-muted">
                {selected.length === 0 ? (
                  <p>Es gibt noch keine Fotos zum Speichern.</p>
                ) : (
                  <p>
                    <span className="font-semibold text-ae-text">{photoCount(selected.length)}</span> in Aufnahme-Reihenfolge
                    {exampleName && (
                      <>
                        , z. B. <span className="font-mono text-xs [overflow-wrap:anywhere] text-ae-text">{exampleName}</span>
                      </>
                    )}
                    .
                  </p>
                )}
                {selected.length > 0 && method === "share" && (
                  <p className="mt-1.5" data-testid="export-method">
                    {shareTargetHint(platform, selected.length)}
                    {ready && batches.length > 1 && ` Die Fotos werden in ${batches.length} Teilen übergeben (je ein Tippen).`}
                  </p>
                )}
                {selected.length > 0 && method === "zip" && !zipInstead && (
                  <p className="mt-1.5" data-testid="export-method">
                    {touch
                      ? `Dieser Browser kann die Fotos nicht teilen – sie werden als ZIP-Datei „${archiveName}“ heruntergeladen.`
                      : `Als ZIP-Datei „${archiveName}“.`}
                  </p>
                )}
                {leftOut.length > 0 && (
                  <p className="mt-1.5 text-ae-text">Ohne: {leftOut.map(shotName).join(", ")}</p>
                )}
                {originals.length > 0 && (
                  <p className="mt-1.5 text-ae-text">
                    Original statt Showroom-Bild: {originals.map(shotName).join(", ")}
                  </p>
                )}
                {plan.counts.original > 0 && (
                  <p className="mt-1.5">
                    Tipp: Mit „Fotos bearbeiten“ entstehen zuerst die Showroom-Bilder – sonst werden die Originale gespeichert.
                  </p>
                )}
              </div>
            )}

            {selected.length > 0 && !ready && failures.length === 0 && (
              <div className="mb-3" role="status">
                <p className="mb-1.5 text-xs text-ae-muted">
                  Fotos werden vorbereitet… {files.length} / {selected.length}
                </p>
                <ProgressBar value={files.length} max={selected.length} label="Fotos werden vorbereitet" />
              </div>
            )}

            {!finished &&
              missing.map(({ item, error }) => (
                <MissingFileNotice
                  key={item.key}
                  item={item}
                  error={error}
                  disabled={busy || progress !== null}
                  onUseOriginal={() => setUseOriginal((current) => withKey(current, item.key))}
                  onLeaveOut={() => setLeaveOut((current) => withKey(current, item.key))}
                />
              ))}

            {transient.length > 0 && !finished && (
              <div role="alert" className="mb-3 rounded-xl border border-ae-danger/30 bg-ae-danger/8 p-3 text-sm">
                <p className="font-semibold text-ae-danger">
                  {transient.length === 1 && transient[0]
                    ? `${shotName(transient[0].item)} konnte nicht geladen werden.`
                    : `${transient.length} Fotos konnten nicht geladen werden.`}
                </p>
                <p className="mt-0.5 text-xs text-ae-muted">{transient[0]?.error.message}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={busy}
                    onClick={() => loader.want(transient.map((failure) => failure.item), { retry: true })}
                  >
                    Erneut versuchen
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy || progress !== null}
                    onClick={() =>
                      setLeaveOut((current) => new Set([...current, ...transient.map((failure) => failure.item.key)]))
                    }
                  >
                    {transient.length === 1 ? "Ohne dieses Foto speichern" : "Ohne diese Fotos speichern"}
                  </Button>
                </div>
              </div>
            )}

            {phase.kind === "failed" && (
              <p role="alert" className="mb-3 text-sm font-medium text-ae-danger">
                {phase.message}
              </p>
            )}

            {plan.counts.retake > 0 && !finished && (
              <label className="mb-3 flex items-start gap-3 rounded-xl border border-ae-danger/30 bg-ae-danger/8 px-3 py-2.5 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5 size-5 shrink-0 accent-ae-blue"
                  checked={includeRetakes}
                  disabled={busy || progress !== null}
                  onChange={(event) => setIncludeRetakes(event.target.checked)}
                />
                <span>
                  {plan.counts.retake === 1
                    ? "Das abgelehnte Foto trotzdem mitspeichern"
                    : `Die ${plan.counts.retake} abgelehnten Fotos trotzdem mitspeichern`}
                  <span className="block text-xs text-ae-muted">
                    Ohne Haken werden sie weggelassen. Besser: neu aufnehmen.
                  </span>
                </span>
              </label>
            )}

            {rows.length > 0 && !finished && (
              <ul className="mb-3 flex flex-col gap-1.5" aria-label="Bitte ansehen">
                {rows.map((row) => {
                  const content = (
                    <>
                      <span className="mt-0.5 shrink-0 rounded-md bg-ae-surface-3 px-1.5 py-0.5 text-[11px] font-bold tabular-nums">
                        {row.numbers}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-semibold">{row.title}</span>
                        <span className={cn("block text-xs font-medium", STATUS_TONE[row.status])}>{row.label}</span>
                        {row.note && <span className="mt-0.5 block text-xs text-ae-muted">{row.note}</span>}
                      </span>
                    </>
                  );
                  const shotKey = row.shotKey;
                  return (
                    <li key={row.key}>
                      {onShowShot && shotKey ? (
                        <button
                          type="button"
                          onClick={() => onShowShot(shotKey)}
                          className="flex w-full items-start gap-2.5 rounded-xl bg-ae-surface-2 px-3 py-2 text-left hover:bg-ae-surface-3"
                        >
                          {content}
                        </button>
                      ) : (
                        <div className="flex items-start gap-2.5 rounded-xl bg-ae-surface-2 px-3 py-2">{content}</div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {/* Phones in landscape: the actions in one row, so the summary keeps its room. */}
          <footer className="pb-safe border-t border-ae-border/70 px-5 pt-3 short:pt-2">
            <div className="mb-3 flex flex-col gap-2 short:mb-2 short:flex-row short:flex-wrap short:items-center">
              {finished ? (
                <Button size="lg" fullWidth className="short:h-11" onClick={close}>
                  Fertig
                </Button>
              ) : (
                <>
                  {progress && (
                    <p className="flex items-start gap-1.5 text-sm text-ae-success short:basis-full" role="status">
                      <CircleCheck className="mt-0.5 size-4 shrink-0" aria-hidden />
                      {`Teil ${progress.next} von ${progress.total} übergeben – jetzt die nächsten ${photoCount(batches[progress.next]?.length ?? 0)} sichern.`}
                    </p>
                  )}
                  <LoadingButton
                    size="lg"
                    fullWidth
                    className="short:h-11 short:w-auto short:flex-1"
                    onClick={handleSave}
                    disabled={!ready}
                    loading={busy}
                    loadingText={phase.kind === "busy" ? phase.text : undefined}
                    icon={
                      method === "share" ? <Share className="size-5" aria-hidden /> : <Download className="size-5" aria-hidden />
                    }
                  >
                    {saveLabel}
                  </LoadingButton>
                  <div
                    className={cn(
                      "grid grid-cols-1 gap-2 short:flex short:shrink-0",
                      method === "share" && "sm:grid-cols-2",
                    )}
                  >
                    <Button variant="secondary" onClick={close} disabled={busy}>
                      Zurück
                    </Button>
                    {method === "share" && (
                      <Button
                        variant="ghost"
                        onClick={() => void saveZip(files)}
                        disabled={!ready || busy}
                        icon={<Download className="size-4" aria-hidden />}
                        className="whitespace-nowrap"
                      >
                        Als ZIP-Datei herunterladen
                      </Button>
                    )}
                  </div>
                </>
              )}
            </div>
          </footer>
        </div>
      )}
    </dialog>
  );
}

/**
 * A file that is gone (e.g. a processed result that was never stored): the
 * other photos stay saveable – leave this one out, or take its original
 * instead of the missing showroom image (the employee's explicit choice).
 */
function MissingFileNotice({
  item,
  error,
  disabled,
  onUseOriginal,
  onLeaveOut,
}: {
  item: ExportItem;
  error: ExportFileError;
  disabled: boolean;
  onUseOriginal: () => void;
  onLeaveOut: () => void;
}) {
  const processed = item.version === "processed" && item.originalFileName !== null;
  return (
    <div role="alert" className="mb-3 rounded-xl border border-ae-danger/30 bg-ae-danger/8 p-3 text-sm">
      <p className="font-semibold text-ae-danger">
        {processed
          ? `Das Showroom-Bild von ${shotName(item)} fehlt.`
          : `${shotName(item)} konnte nicht geladen werden.`}
      </p>
      <p className="mt-0.5 text-xs text-ae-muted">
        {processed ? `${error.message} Stattdessen das Original speichern – oder das Foto weglassen.` : error.message}
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        {processed && (
          <Button variant="secondary" size="sm" disabled={disabled} onClick={onUseOriginal}>
            Original verwenden
          </Button>
        )}
        <Button variant={processed ? "ghost" : "secondary"} size="sm" disabled={disabled} onClick={onLeaveOut}>
          Ohne dieses Foto speichern
        </Button>
      </div>
    </div>
  );
}
