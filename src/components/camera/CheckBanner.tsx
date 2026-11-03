import { CircleCheck, Info, RotateCcw, X } from "lucide-react";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import type { ShotCheckState } from "@/lib/processing/shot-check";

export interface CheckBannerState {
  /** Changes with every finished check (restarts the banner's timer). */
  id: string;
  shotKey: string;
  kind: "ok" | "retake" | "error";
  text: string;
}

/**
 * Short message at the top of the viewfinder when the automatic check of a
 * shot finished. A rejected shot's banner is a button: it jumps to that shot
 * for the retake. Never blocks the shutter.
 */
export function CheckBanner({
  banner,
  onSelect,
  onDismiss,
}: {
  banner: CheckBannerState;
  onSelect: (shotKey: string) => void;
  onDismiss: () => void;
}) {
  const Icon = banner.kind === "ok" ? CircleCheck : banner.kind === "retake" ? RotateCcw : Info;
  return (
    <div
      className="pointer-events-none absolute inset-x-3 top-3 z-30 flex animate-slide-up justify-center"
      role={banner.kind === "retake" ? "alert" : "status"}
    >
      <div
        className={cn(
          "pointer-events-auto flex max-w-md items-start gap-1 rounded-xl text-xs leading-snug font-semibold shadow-lg backdrop-blur",
          banner.kind === "ok" && "bg-ae-success/95 text-black",
          banner.kind === "retake" && "bg-ae-danger/95 text-white",
          banner.kind === "error" && "bg-black/80 text-white",
        )}
      >
        {banner.kind === "retake" ? (
          <button
            type="button"
            onClick={() => onSelect(banner.shotKey)}
            className="flex min-w-0 flex-1 items-start gap-2 py-2 pl-3 text-left"
          >
            <Icon className="mt-px size-4 shrink-0" aria-hidden />
            <span>
              {banner.text}
              <span className="mt-0.5 block underline underline-offset-2">Jetzt wiederholen</span>
            </span>
          </button>
        ) : (
          <p className="flex min-w-0 flex-1 items-start gap-2 py-2 pl-3">
            <Icon className="mt-px size-4 shrink-0" aria-hidden />
            <span>{banner.text}</span>
          </p>
        )}
        <button
          type="button"
          onClick={onDismiss}
          className="flex size-8 shrink-0 items-center justify-center rounded-lg"
          aria-label="Hinweis schließen"
        >
          <X className="size-3.5" aria-hidden />
        </button>
      </div>
    </div>
  );
}

/**
 * State of the current shot in the viewfinder (instead of "Bereits
 * aufgenommen"): being checked, passed, or – with the processor's reason –
 * to be retaken.
 */
export function ShotCheckChip({ state }: { state: ShotCheckState }) {
  if (state.kind === "retake") {
    return (
      <p className="flex max-w-sm items-start gap-1.5 rounded-xl bg-ae-danger/95 px-3 py-1.5 text-xs leading-snug font-semibold text-white shadow">
        <RotateCcw className="mt-px size-3.5 shrink-0" aria-hidden />
        <span className="line-clamp-4">
          Bitte neu aufnehmen: <span className="font-medium">{state.reason}</span>
        </span>
      </p>
    );
  }
  return (
    <span className="flex max-w-sm items-start gap-1 rounded-2xl bg-black/60 px-2.5 py-1 text-xs font-semibold text-white backdrop-blur">
      {state.kind === "checking" ? (
        <Spinner className="mt-px size-3.5 shrink-0" />
      ) : state.kind === "ok" ? (
        <CircleCheck className="mt-px size-3.5 shrink-0 text-ae-success" aria-hidden />
      ) : (
        <CircleCheck className="mt-px size-3.5 shrink-0 text-ae-blue" aria-hidden />
      )}
      <span>
        {state.kind === "checking"
          ? "Aufgenommen · wird geprüft…"
          : state.kind === "ok"
            ? state.hint
              ? "Geprüft – passt · Ergebnis bitte später prüfen"
              : "Geprüft – passt"
            : state.kind === "error"
              ? "Bereits aufgenommen · Prüfung nicht möglich"
              : "Bereits aufgenommen"}
      </span>
    </span>
  );
}
