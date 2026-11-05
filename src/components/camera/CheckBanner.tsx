import { ChevronDown, CircleCheck, Info, RotateCcw, Save, TriangleAlert, X } from "lucide-react";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import { SHOT_CHECK_LABELS, type ShotCheckState } from "@/lib/processing/shot-check";

export interface CheckBannerState {
  /** Changes with every finished check (restarts the banner's timer). */
  id: string;
  shotKey: string;
  kind: "ok" | "retake" | "error";
  text: string;
}

type Tone = "ok" | "hint" | "retake" | "error" | "saved" | "checking" | "neutral";

/** What the status line shows: the finished check (banner) or the current shot's state. */
export interface CheckStatusContent {
  /** Changes when the content changes (an expanded line closes then). */
  id: string;
  tone: Tone;
  /** The one-line label (bold). */
  label: string;
  /** More text (shown on the line as far as it fits, in full when expanded). */
  detail: string | null;
  /** A rejected OTHER shot: "Jetzt wiederholen" jumps there. */
  retakeShotKey: string | null;
}

/**
 * The content of the camera's check status line (pure): a banner of a
 * finished check while it is shown, else the current shot's own state once
 * it is captured. null: nothing to show.
 */
export function checkStatusContent(
  banner: CheckBannerState | null,
  shot: { key: string },
  state: ShotCheckState,
  captured: boolean,
): CheckStatusContent | null {
  if (banner) {
    return {
      id: `banner:${banner.id}`,
      tone: banner.kind,
      label: banner.text,
      detail: null,
      retakeShotKey: banner.kind === "retake" && banner.shotKey !== shot.key ? banner.shotKey : null,
    };
  }
  if (!captured) return null;
  const id = `shot:${shot.key}:${state.kind}`;
  switch (state.kind) {
    case "checking":
      return { id, tone: "checking", label: "Aufgenommen · wird geprüft…", detail: null, retakeShotKey: null };
    case "ok":
      return state.hint
        ? { id, tone: "hint", label: SHOT_CHECK_LABELS.hint, detail: state.hint, retakeShotKey: null }
        : { id, tone: "ok", label: SHOT_CHECK_LABELS.ok, detail: null, retakeShotKey: null };
    case "saved":
      return { id, tone: "saved", label: SHOT_CHECK_LABELS.saved, detail: null, retakeShotKey: null };
    case "retake":
      return { id, tone: "retake", label: "Bitte neu aufnehmen:", detail: state.reason, retakeShotKey: null };
    case "error":
      return { id, tone: "error", label: "Bereits aufgenommen · Prüfung nicht möglich", detail: state.reason, retakeShotKey: null };
    default:
      return { id, tone: "neutral", label: "Bereits aufgenommen", detail: null, retakeShotKey: null };
  }
}

const TONE_CLASSES: Record<Tone, string> = {
  ok: "bg-ae-success/95 text-black",
  hint: "bg-ae-warning/95 text-black",
  retake: "bg-ae-danger/95 text-white",
  error: "bg-white/15 text-white",
  saved: "bg-white/12 text-white",
  checking: "bg-white/12 text-white",
  neutral: "bg-white/12 text-white",
};

function ToneIcon({ tone }: { tone: Tone }) {
  switch (tone) {
    case "checking":
      return <Spinner className="size-3.5 shrink-0" />;
    case "ok":
      return <CircleCheck className="size-3.5 shrink-0" aria-hidden />;
    case "hint":
      return <TriangleAlert className="size-3.5 shrink-0" aria-hidden />;
    case "retake":
      return <RotateCcw className="size-3.5 shrink-0" aria-hidden />;
    case "saved":
      return <Save className="size-3.5 shrink-0 text-ae-blue" aria-hidden />;
    default:
      return <Info className="size-3.5 shrink-0" aria-hidden />;
  }
}

const PANEL_ID = "camera-check-details";

/**
 * The automatic check in the camera header, never on the framing guide: ONE
 * line (truncated) with the result of a check that just finished or the
 * current shot's state. Tapping it opens the full text below the header
 * (`expanded`) – for a rejected other shot with "Jetzt wiederholen". The
 * current shot, the progress and the shutter stay free.
 */
export function CheckStatusLine({
  content,
  expanded,
  onToggle,
  className,
}: {
  content: CheckStatusContent | null;
  expanded: boolean;
  onToggle: () => void;
  className?: string;
}) {
  return (
    <div
      className={cn("flex min-w-0 items-center justify-center", className)}
      role={content?.tone === "retake" && content.id.startsWith("banner:") ? "alert" : "status"}
    >
      {content && (
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          aria-controls={expanded ? PANEL_ID : undefined}
          data-tone={content.tone}
          className={cn(
            "flex h-6 max-w-full min-w-0 animate-fade-in items-center gap-1.5 rounded-full px-2.5 text-xs leading-none font-semibold backdrop-blur",
            TONE_CLASSES[content.tone],
          )}
        >
          <ToneIcon tone={content.tone} />
          <span className="min-w-0 truncate">
            {content.label}
            {content.detail && <span className="font-medium"> {content.detail}</span>}
          </span>
          <ChevronDown className={cn("size-3.5 shrink-0 transition-transform", expanded && "rotate-180")} aria-hidden />
        </button>
      )}
    </div>
  );
}

/** The full text of the status line, opened by tapping it (overlays the top of the viewfinder only while open). */
export function CheckStatusDetails({
  content,
  onSelect,
  onClose,
}: {
  content: CheckStatusContent;
  onSelect: (shotKey: string) => void;
  onClose: () => void;
}) {
  return (
    <div className="pointer-events-none absolute inset-x-3 top-full z-30 mt-1 flex animate-fade-in justify-center">
      <div
        id={PANEL_ID}
        className={cn(
          "pointer-events-auto flex w-full max-w-md items-start gap-2 rounded-xl py-2 pl-3 text-xs leading-snug font-semibold shadow-lg backdrop-blur",
          TONE_CLASSES[content.tone],
          content.tone !== "ok" && content.tone !== "hint" && content.tone !== "retake" && "bg-neutral-900/95",
        )}
      >
        <span className="mt-px">
          <ToneIcon tone={content.tone} />
        </span>
        <div className="min-w-0 flex-1">
          <p>
            {content.label}
            {content.detail && <span className="font-medium"> {content.detail}</span>}
          </p>
          {content.retakeShotKey && (
            <button
              type="button"
              onClick={() => onSelect(content.retakeShotKey as string)}
              className="mt-1.5 underline underline-offset-2"
            >
              Jetzt wiederholen
            </button>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="-my-1 flex size-8 shrink-0 items-center justify-center rounded-lg"
          aria-label="Hinweis schließen"
        >
          <X className="size-3.5" aria-hidden />
        </button>
      </div>
    </div>
  );
}
