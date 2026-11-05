"use client";

import { Download, Share } from "lucide-react";
import { useEffect, useState } from "react";
import { Spinner } from "@/components/ui/Spinner";
import { useTouchDevice } from "@/hooks/use-touch-device";
import { getAppServices, getShotTemplate } from "@/lib/app-services";
import { cn } from "@/lib/cn";
import type { PhotoVersion, VehiclePhoto } from "@/lib/domain/types";
import { toUserMessage } from "@/lib/errors";
import {
  canShareFiles,
  canShareFileTypes,
  downloadBlob,
  hasFreshTap,
  ShareFailedError,
  shareFiles,
} from "@/lib/export/device-save";
import { exportMimeType, loadExportFile } from "@/lib/export/export-files";
import { exportFileNameFor, type ExportVehicle } from "@/lib/export/listing-export";

/**
 * - idle:    "Speichern"
 * - loading: the file is being loaded
 * - ready:   loaded, but the tap has expired meanwhile – a second tap opens the share sheet
 * - sharing: the share sheet is open
 */
type State =
  | { kind: "idle"; message?: { text: string; tone: "info" | "error" } }
  | { kind: "loading" }
  | { kind: "ready"; file: File }
  | { kind: "sharing" };

const MESSAGE_MS = 4000;

/**
 * "Speichern" in the large view: the version shown (original or processed)
 * as AE_{ref}_{nn}_{shot}.{ext} – the same name as in "Alle Fotos speichern"
 * – through the share sheet on phones whose browser can share it, as a
 * download with that file name everywhere else.
 */
export function SavePhotoButton({
  vehicle,
  photo,
  version,
  className,
}: {
  vehicle: ExportVehicle;
  photo: VehiclePhoto;
  version: PhotoVersion;
  className?: string;
}) {
  const touch = useTouchDevice();
  const fileName = exportFileNameFor(getShotTemplate(), vehicle, photo, version);
  // Share icon only where the share sheet will open (known from the name and type).
  const canShare = touch && canShareFileTypes([{ name: fileName, type: exportMimeType({ version, fileName }) }]);
  const target = `${photo.id}:${version}`;
  const [state, setState] = useState<State>({ kind: "idle" });
  const [stateFor, setStateFor] = useState(target);

  // A state belongs to the photo and version shown (adjusted during render).
  if (stateFor !== target) {
    setStateFor(target);
    setState({ kind: "idle" });
  }

  const message = state.kind === "idle" ? state.message : undefined;
  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(() => setState((current) => (current.kind === "idle" ? { kind: "idle" } : current)), MESSAGE_MS);
    return () => clearTimeout(timer);
  }, [message]);

  async function share(file: File) {
    setState({ kind: "sharing" });
    try {
      const outcome = await shareFiles([file]);
      setState(
        outcome === "shared"
          ? { kind: "idle", message: { text: `${file.name} an das Teilen-Menü übergeben.`, tone: "info" } }
          : { kind: "idle" },
      );
    } catch (error) {
      // The share sheet refused: the download is the way out.
      downloadBlob(file, file.name);
      setState({
        kind: "idle",
        message: {
          text: error instanceof ShareFailedError ? "Teilen nicht möglich – das Foto wird heruntergeladen." : toUserMessage(error),
          tone: "error",
        },
      });
    }
  }

  async function handleClick() {
    if (state.kind === "ready") {
      void share(state.file);
      return;
    }
    if (state.kind !== "idle") return;
    setState({ kind: "loading" });
    let file: File;
    try {
      file = await loadExportFile(getAppServices().backend.data, { photo, version, fileName });
    } catch (error) {
      setState({ kind: "idle", message: { text: toUserMessage(error), tone: "error" } });
      return;
    }
    if (canShare && canShareFiles([file])) {
      // Loading took too long for the tap: one more tap opens the share sheet.
      if (!hasFreshTap()) {
        setState({ kind: "ready", file });
        return;
      }
      void share(file);
      return;
    }
    downloadBlob(file, file.name);
    setState({ kind: "idle", message: { text: `Gespeichert als ${file.name}`, tone: "info" } });
  }

  const busy = state.kind === "loading" || state.kind === "sharing";
  const Icon = canShare ? Share : Download;

  return (
    <div className={cn("relative", className)}>
      <button
        type="button"
        onClick={() => void handleClick()}
        disabled={busy}
        aria-busy={busy}
        className={cn(
          "flex h-10 items-center gap-1.5 rounded-xl px-3 text-sm font-semibold hover:bg-ae-surface-2 disabled:opacity-60",
          state.kind === "ready" && "bg-ae-blue text-white hover:bg-ae-blue-strong",
        )}
        aria-label={state.kind === "ready" ? "Foto jetzt speichern" : "Foto speichern"}
      >
        {busy ? <Spinner className="size-4" /> : <Icon className="size-4" aria-hidden />}
        <span>{state.kind === "ready" ? "Jetzt speichern" : "Speichern"}</span>
      </button>
      {message && (
        <p
          role={message.tone === "error" ? "alert" : "status"}
          className={cn(
            "absolute top-full right-0 z-10 mt-2 w-64 rounded-lg border px-3 py-2 text-xs font-medium shadow-[var(--shadow-card)] [overflow-wrap:anywhere]",
            message.tone === "error"
              ? "border-ae-danger/40 bg-ae-surface text-ae-danger"
              : "border-ae-border bg-ae-surface text-ae-text",
          )}
        >
          {message.text}
        </p>
      )}
    </div>
  );
}
