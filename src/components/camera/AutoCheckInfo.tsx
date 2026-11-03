import { ShieldOff, X } from "lucide-react";
import { AccessCodeForm } from "@/components/processing/AccessCodeForm";

/**
 * Why the automatic check is off – opened from the discreet hint in the
 * camera header. In demo mode without a stored access code the code can be
 * entered right here (saved on this device, like on "Fotos bearbeiten").
 */
export function AutoCheckInfo({
  message,
  askAccessCode,
  onAccessCode,
  onClose,
}: {
  message: string;
  askAccessCode: boolean;
  onAccessCode: (code: string) => void;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-[60] flex animate-fade-in items-center justify-center bg-black/60 p-4"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="max-h-full w-full max-w-md overflow-y-auto rounded-2xl border border-white/10 bg-ae-surface p-4 text-sm text-ae-text shadow-xl"
        role="dialog"
        aria-labelledby="auto-check-info-title"
      >
        <div className="mb-2 flex items-start gap-2">
          <ShieldOff className="mt-0.5 size-4 shrink-0 text-ae-muted" aria-hidden />
          <p id="auto-check-info-title" className="flex-1 font-semibold">
            Automatische Prüfung aus
          </p>
          <button
            type="button"
            onClick={onClose}
            className="-mt-1 -mr-1 flex size-8 items-center justify-center rounded-lg text-ae-muted hover:bg-white/10"
            aria-label="Schließen"
          >
            <X className="size-4" aria-hidden />
          </button>
        </div>
        <p className="text-ae-muted">{message}</p>
        {askAccessCode && (
          <div className="mt-3">
            <AccessCodeForm error={null} onSubmit={onAccessCode} />
          </div>
        )}
      </div>
    </div>
  );
}
