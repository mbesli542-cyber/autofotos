import { CircleCheck, PlugZap, TriangleAlert } from "lucide-react";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import { PROCESSING_NOT_CONNECTED_MESSAGE } from "@/lib/processing/types";

export type ProcessorConnectionState = "checking" | "connected" | "disconnected";

const CHIP_STYLES: Record<ProcessorConnectionState, string> = {
  checking: "border-ae-border-strong bg-ae-surface-2 text-ae-muted",
  connected: "border-ae-success/35 bg-ae-success/12 text-ae-success",
  disconnected: "border-ae-warning/35 bg-ae-warning/12 text-ae-warning",
};

const CHIP_LABELS: Record<ProcessorConnectionState, string> = {
  checking: "Bildverarbeitung wird geprüft…",
  connected: "Reale Bildverarbeitung",
  disconnected: "Showroom-Prozessor nicht verbunden",
};

/**
 * Status of the real image processor at the top of "Fotos bearbeiten":
 * green chip when connected, amber chip + message when not, and the
 * showroom problem (e.g. master photo missing) when processing cannot run.
 */
export function ProcessingStatusPanel({
  state,
  showroomError,
}: {
  state: ProcessorConnectionState;
  showroomError: string | null;
}) {
  return (
    <div className="flex flex-col gap-3" role="status" aria-live="polite">
      <span
        className={cn(
          "inline-flex w-fit items-center gap-1.5 rounded-full border px-3 py-1 text-sm font-semibold",
          CHIP_STYLES[state],
        )}
      >
        {state === "checking" && <Spinner className="size-3.5" />}
        {state === "connected" && <CircleCheck className="size-4" aria-hidden />}
        {state === "disconnected" && <PlugZap className="size-4" aria-hidden />}
        {CHIP_LABELS[state]}
      </span>

      {state === "disconnected" && (
        <div className="flex gap-3 rounded-xl border border-ae-warning/30 bg-ae-warning/8 p-4 text-sm">
          <TriangleAlert className="size-5 shrink-0 text-ae-warning" aria-hidden />
          <div className="text-ae-muted">
            <p className="font-semibold text-ae-text">{PROCESSING_NOT_CONNECTED_MESSAGE}</p>
            <p className="mt-1">
              Es werden keine Fotos bearbeitet oder gespeichert, bis der AutoExperten Showroom-Prozessor
              angebunden ist. Die Originalfotos bleiben unverändert.
            </p>
          </div>
        </div>
      )}

      {state === "connected" && showroomError && (
        <div className="flex gap-3 rounded-xl border border-ae-warning/30 bg-ae-warning/8 p-4 text-sm">
          <TriangleAlert className="size-5 shrink-0 text-ae-warning" aria-hidden />
          <div className="text-ae-muted">
            <p className="font-semibold text-ae-text">{showroomError}</p>
            <p className="mt-1">
              Die Bearbeitung ist erst möglich, wenn das AutoExperten Showroom-Foto auf dem Server hinterlegt ist.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
