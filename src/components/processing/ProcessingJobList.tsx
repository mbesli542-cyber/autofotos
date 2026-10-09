import { Camera, Check, Clock, RotateCw, TriangleAlert, WandSparkles } from "lucide-react";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import { SHOT_TREATMENT_LABELS } from "@/lib/processing/shot-treatment";
import type { PhotoRunState, PhotoRunStatus } from "@/features/processing/use-processing-run";

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
 * What a failed shot offers when a new photo is the fix (e.g. the processor's
 * quality gate rejected it):
 * - "retake": open the guided camera for exactly this shot
 * - "saving": the new photo is still being saved (`failed`: upload failed, retry)
 * - "ready":  the new photo is saved and can be processed
 */
export type JobRetakeAction =
  | { kind: "retake"; href: string }
  | { kind: "saving"; failed: boolean; onRetryUpload: () => void }
  | { kind: "ready"; onProcess: () => void };

function StatusIcon({ status }: { status: PhotoRunStatus }) {
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
  if (action.kind === "saving") {
    return action.failed ? (
      <div className="mt-1">
        <p className="text-xs text-ae-warning">Das neue Foto konnte noch nicht gespeichert werden.</p>
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
 * aufnehmen" (and the follow-up states) to shots a new photo would fix.
 */
export function ProcessingJobList({
  items,
  retakeAction,
}: {
  items: readonly PhotoRunState[];
  retakeAction?: (item: PhotoRunState) => JobRetakeAction | null;
}) {
  return (
    <ul className="divide-y divide-ae-border/70 rounded-2xl border border-ae-border bg-ae-surface" aria-label="Bearbeitungsfortschritt">
      {items.map((item) => {
        const action = retakeAction?.(item) ?? null;
        // Once a new photo exists, the old rejection no longer applies.
        const replaced = action !== null && action.kind !== "retake";
        return (
          <li key={item.shotKey} className="flex items-start gap-3 px-4 py-2.5">
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
              {item.error && !replaced && <p className="mt-0.5 text-xs text-ae-danger">{item.error}</p>}
              {action && <RetakeActionView action={action} title={item.title} />}
            </div>
            <span
              className={cn(
                "flex shrink-0 items-center gap-1.5 pt-0.5 text-xs font-medium",
                replaced
                  ? "text-ae-muted"
                  : item.status === "complete"
                    ? "text-ae-success"
                    : item.status === "failed"
                      ? "text-ae-danger"
                      : "text-ae-muted",
              )}
            >
              {replaced ? <Camera className="size-4 text-ae-subtle" aria-hidden /> : <StatusIcon status={item.status} />}
              {replaced ? "Neues Foto" : LABELS[item.status]}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
