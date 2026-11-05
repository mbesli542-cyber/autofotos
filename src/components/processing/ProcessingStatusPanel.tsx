import { CircleCheck, CloudOff, PlugZap, RefreshCw, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import {
  describeProcessorConnection,
  type ProcessorConnectionState,
} from "@/lib/processing/processor-connection";

export type { ProcessorConnectionState };

const CHIP_STYLES: Record<ProcessorConnectionState, string> = {
  checking: "border-ae-border-strong bg-ae-surface-2 text-ae-muted",
  connected: "border-ae-success/35 bg-ae-success/12 text-ae-success",
  offline: "border-ae-warning/35 bg-ae-warning/12 text-ae-warning",
  unreachable: "border-ae-warning/35 bg-ae-warning/12 text-ae-warning",
  misconfigured: "border-ae-warning/35 bg-ae-warning/12 text-ae-warning",
  not_configured: "border-ae-warning/35 bg-ae-warning/12 text-ae-warning",
};

/**
 * Status of the showroom processing at the top of "Fotos bearbeiten": green
 * chip when connected; otherwise why not – offline, the showroom PC not
 * answering (with "Erneut prüfen"), not usable (`connectionError`) or not
 * set up at all – and the showroom problem (e.g. plate set missing) when
 * processing cannot run. Texts: src/lib/processing/processor-connection.ts.
 */
export function ProcessingStatusPanel({
  state,
  showroomError,
  connectionError = null,
  onRetry,
}: {
  state: ProcessorConnectionState;
  showroomError: string | null;
  connectionError?: string | null;
  /** Asks the status again ("Erneut prüfen"). */
  onRetry?: () => void;
}) {
  const text = describeProcessorConnection(state, connectionError);
  const ChipIcon = state === "offline" ? CloudOff : PlugZap;
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
        {state !== "checking" && state !== "connected" && <ChipIcon className="size-4" aria-hidden />}
        {text.label}
      </span>

      {text.message && (
        <div className="flex gap-3 rounded-xl border border-ae-warning/30 bg-ae-warning/8 p-4 text-sm">
          <TriangleAlert className="size-5 shrink-0 text-ae-warning" aria-hidden />
          <div className="min-w-0 flex-1 text-ae-muted">
            <p className="font-semibold text-ae-text">{text.message}</p>
            {text.detail && <p className="mt-1">{text.detail}</p>}
            {text.canRetry && onRetry && (
              <Button
                variant="secondary"
                className="mt-3"
                onClick={onRetry}
                icon={<RefreshCw className="size-4" aria-hidden />}
              >
                Erneut prüfen
              </Button>
            )}
          </div>
        </div>
      )}

      {state === "connected" && showroomError && (
        <div className="flex gap-3 rounded-xl border border-ae-warning/30 bg-ae-warning/8 p-4 text-sm">
          <TriangleAlert className="size-5 shrink-0 text-ae-warning" aria-hidden />
          <div className="text-ae-muted">
            <p className="font-semibold text-ae-text">{showroomError}</p>
            <p className="mt-1">
              Die Bearbeitung ist erst möglich, wenn der AutoExperten Showroom auf dem Server vollständig
              eingerichtet ist.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
