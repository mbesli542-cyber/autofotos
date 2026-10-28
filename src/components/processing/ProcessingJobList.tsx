"use client";

import { Camera, Check, Clock, RotateCw, TriangleAlert, WandSparkles } from "lucide-react";
import { useEffect, useRef } from "react";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import type { PhotoRunState, PhotoRunStatus } from "@/lib/processing/processing-runs";
import { needsResultCheck } from "@/lib/processing/processing-warnings";
import { SHOT_TREATMENT_LABELS } from "@/lib/processing/shot-treatment";
import { ProcessingWarningList } from "./ProcessingWarningList";

const LABELS: Record<PhotoRunStatus, string> = {
  waiting: "Wartend",
  preparing: "Wird hochgeladen",
  queued: "In Warteschlange",
  processing: "In Bearbeitung",
  saving: "Wird gespeichert",
  complete: "Fertig",
  failed: "Fehlgeschlagen",
};

/**
 * What a finished shot offers:
 * - "retake": a new photo is the fix (e.g. the processor's quality gate
 *             rejected it) → open the guided camera for exactly this shot
 * - "saving": a new photo of the shot is still being saved (`failed`: upload failed, retry;
 *             `error`: the upload's German message)
 * - "ready":  a new photo replaced the rejected or processed one and can be processed
 * - "retry":  a temporary failure (connection, busy processor) → process the same photo again
 */
export type JobRetakeAction =
  | { kind: "retake"; href: string }
  | { kind: "saving"; failed: boolean; error?: string | null; onRetryUpload: () => void }
  | { kind: "ready"; onProcess: () => void }
  | { kind: "retry"; onRetry: () => void };

function StatusIcon({ status, check }: { status: PhotoRunStatus; check: boolean }) {
  if (status === "complete" && check) return <TriangleAlert className="size-4 text-ae-warning" aria-hidden />;
  if (status === "complete") return <Check className="size-4 text-ae-success" strokeWidth={3} aria-hidden />;
  if (status === "failed") return <TriangleAlert className="size-4 text-ae-danger" aria-hidden />;
  if (status === "waiting" || status === "queued") return <Clock className="size-4 text-ae-subtle" aria-hidden />;
  return <Spinner className="size-4 text-ae-blue" />;
}

function RetakeActionView({ action, title }: { action: JobRetakeAction; title: string }) {
  if (action.kind === "retake") {
    return (
      <ButtonLink
        href={action.href}
        variant="secondary"
        size="sm"
        className="mt-2 h-10"
        icon={<Camera className="size-4" aria-hidden />}
        aria-label={`Foto neu aufnehmen: ${title}`}
      >
        Foto neu aufnehmen
      </ButtonLink>
    );
  }
  if (action.kind === "retry") {
    return (
      <Button
        variant="ghost"
        size="sm"
        className="mt-1 -ml-3 h-10"
        onClick={action.onRetry}
        icon={<RotateCw className="size-4" aria-hidden />}
        aria-label={`Erneut versuchen: ${title}`}
      >
        Erneut versuchen
      </Button>
    );
  }
  if (action.kind === "saving") {
    return action.failed ? (
      <div className="mt-1">
        <p className="text-xs text-ae-warning">
          {action.error ?? "Das neue Foto konnte noch nicht gespeichert werden."}
        </p>
        <Button
          variant="ghost"
          size="sm"
          className="mt-1 -ml-3 h-10"
          onClick={action.onRetryUpload}
          icon={<RotateCw className="size-4" aria-hidden />}
        >
          Erneut versuchen
        </Button>
      </div>
    ) : (
      <p className="mt-1 flex items-center gap-1.5 text-xs text-ae-muted" role="status">
        <Spinner className="size-3" />
        Neues Foto wird gespeichert…
      </p>
    );
  }
  return (
    <div className="mt-1">
      <p className="text-xs text-ae-muted">Neues Foto aufgenommen.</p>
      <Button
        size="sm"
        className="mt-2 h-10"
        onClick={action.onProcess}
        icon={<WandSparkles className="size-4" aria-hidden />}
        aria-label={`Neues Foto bearbeiten: ${title}`}
      >
        Neues Foto bearbeiten
      </Button>
    </div>
  );
}

/**
 * Per-photo progress of a processing run. `retakeAction` adds "Foto neu
 * aufnehmen" (and the follow-up states) to shots a new photo would fix and
 * "Erneut versuchen" to temporary failures. A saved result shows the
 * processor's hints on it (e.g. "bitte das Ergebnis prüfen").
 */
export function ProcessingJobList({
  items,
  retakeAction,
}: {
  items: readonly PhotoRunState[];
  retakeAction?: (item: PhotoRunState) => JobRetakeAction | null;
}) {
  const listRef = useRef<HTMLUListElement>(null);
  /** Rows with "Neues Foto bearbeiten" that were already brought into view. */
  const shownReady = useRef<ReadonlySet<string>>(new Set());
  const actions = items.map((item) => retakeAction?.(item) ?? null);
  const readyShots = items
    .flatMap((item, index) => (actions[index]?.kind === "ready" ? [item.shotKey] : []))
    .join(" ");

  useEffect(() => {
    // Back from "Foto neu aufnehmen" the page starts at the top: bring a row that just got
    // "Neues Foto bearbeiten" to the middle of the screen (clear of the bottom navigation) –
    // once, not again each time the page is shown.
    const ready = readyShots ? readyShots.split(" ") : [];
    const next = ready.find((shotKey) => !shownReady.current.has(shotKey));
    shownReady.current = new Set(ready);
    if (!next) return;
    const row = Array.from(listRef.current?.children ?? []).find(
      (element) => element instanceof HTMLElement && element.dataset.shotKey === next,
    );
    row?.scrollIntoView({ block: "center" });
  }, [readyShots]);

  return (
    <ul
      ref={listRef}
      className="divide-y divide-ae-border/70 rounded-2xl border border-ae-border bg-ae-surface"
      aria-label="Bearbeitungsfortschritt"
    >
      {items.map((item, index) => {
        const action = actions[index] ?? null;
        // Once a new photo exists, the old result or rejection no longer applies.
        const replaced = action?.kind === "saving" || action?.kind === "ready";
        const check = !replaced && item.status === "complete" && needsResultCheck(item.warnings);
        return (
          <li key={item.shotKey} data-shot-key={item.shotKey} className="flex items-start gap-3 px-4 py-2.5">
            <span className="w-7 shrink-0 pt-0.5 text-xs font-bold text-ae-subtle tabular-nums">
              {item.shotOrder > 100 ? `+${item.shotOrder - 100}` : String(item.shotOrder).padStart(2, "0")}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{item.title}</p>
              {item.treatment === "original_environment" && (
                <p className="truncate text-xs text-ae-subtle">{SHOT_TREATMENT_LABELS.original_environment}</p>
              )}
              {item.status === "processing" && (
                <div className="mt-1 h-1 overflow-hidden rounded-full bg-ae-surface-3">
                  <div className="h-full bg-ae-blue transition-[width]" style={{ width: `${Math.round(item.progress * 100)}%` }} />
                </div>
              )}
              {item.error && !replaced && (
                <p className="mt-0.5 text-xs text-ae-danger [overflow-wrap:anywhere]">{item.error}</p>
              )}
              {!replaced && item.status === "complete" && (
                <ProcessingWarningList warnings={item.warnings} className="mt-1" />
              )}
              {action && <RetakeActionView action={action} title={item.title} />}
            </div>
            <span
              className={cn(
                "flex shrink-0 items-center gap-1.5 pt-0.5 text-xs font-medium",
                replaced
                  ? "text-ae-muted"
                  : check
                    ? "text-ae-warning"
                    : item.status === "complete"
                      ? "text-ae-success"
                      : item.status === "failed"
                        ? "text-ae-danger"
                        : "text-ae-muted",
              )}
            >
              {replaced ? (
                <Camera className="size-4 text-ae-subtle" aria-hidden />
              ) : (
                <StatusIcon status={item.status} check={check} />
              )}
              {replaced ? "Neues Foto" : LABELS[item.status]}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
