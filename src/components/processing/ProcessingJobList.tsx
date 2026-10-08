import { Check, Clock, TriangleAlert } from "lucide-react";
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

function StatusIcon({ status }: { status: PhotoRunStatus }) {
  if (status === "complete") return <Check className="size-4 text-ae-success" strokeWidth={3} aria-hidden />;
  if (status === "failed") return <TriangleAlert className="size-4 text-ae-danger" aria-hidden />;
  if (status === "waiting" || status === "queued") return <Clock className="size-4 text-ae-subtle" aria-hidden />;
  return <Spinner className="size-4 text-ae-blue" />;
}

/** Per-photo progress of a processing run. */
export function ProcessingJobList({ items }: { items: readonly PhotoRunState[] }) {
  return (
    <ul className="divide-y divide-ae-border/70 rounded-2xl border border-ae-border bg-ae-surface" aria-label="Bearbeitungsfortschritt">
      {items.map((item) => (
        <li key={item.photoId} className="flex items-center gap-3 px-4 py-2.5">
          <span className="w-7 shrink-0 text-xs font-bold text-ae-subtle tabular-nums">
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
            {item.error && <p className="mt-0.5 text-xs text-ae-danger">{item.error}</p>}
          </div>
          <span
            className={cn(
              "flex shrink-0 items-center gap-1.5 text-xs font-medium",
              item.status === "complete" ? "text-ae-success" : item.status === "failed" ? "text-ae-danger" : "text-ae-muted",
            )}
          >
            <StatusIcon status={item.status} />
            {LABELS[item.status]}
          </span>
        </li>
      ))}
    </ul>
  );
}
