"use client";

import {
  Bug,
  CircleCheck,
  Download,
  EyeOff,
  FileJson,
  Image as ImageIcon,
  ImageUp,
  Maximize2,
  ServerCrash,
  ShieldCheck,
  TriangleAlert,
  WandSparkles,
} from "lucide-react";
import { useEffect, useRef, useState, type ChangeEvent, type ReactNode } from "react";
import { AppHeader } from "@/components/layout/AppHeader";
import { PageContainer } from "@/components/layout/PageContainer";
import { buttonClasses } from "@/components/ui/Button";
import { LoadingButton } from "@/components/ui/LoadingButton";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { Spinner } from "@/components/ui/Spinner";
import { getShotTemplate } from "@/lib/app-services";
import { cn } from "@/lib/cn";
import { AppError, toUserMessage } from "@/lib/errors";
import {
  DEFAULT_DEV_TEST_PRESET,
  DEFAULT_DEV_TEST_SHOT_KEY,
  DEV_TEST_API,
  DEV_TEST_MAX_UPLOAD_BYTES,
  DEV_TEST_MAX_UPLOAD_MB,
  DEV_TEST_PRESETS,
  isDevTestPresetId,
  isValidDebugFileName,
  showroomPreviewWidth,
  type DevTestPresetId,
  type ProcessorErrorBody,
  type ProcessorHealth,
  type ProcessorJob,
  type ShowroomSource,
} from "@/lib/processing/dev-test-types";
import { PROCESSING_JOB_STATUS_LABELS, isTerminalJobStatus } from "@/lib/processing/types";
import { getOrderedShots } from "@/lib/shots/shot-template";

const POLL_INTERVAL_MS = 1_000;
const POLL_TIMEOUT_MS = 10 * 60_000;
/** Consecutive failed status requests tolerated before giving up. */
const MAX_POLL_ERRORS = 3;

const SELECT_CLASSES =
  "h-12 w-full appearance-none rounded-xl border border-ae-border bg-ae-surface-2 bg-[url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' fill='none' stroke='%23a3aab5' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m4 6 4 4 4-4'/%3E%3C/svg%3E\")] bg-[length:16px] bg-[right_14px_center] bg-no-repeat pr-10 pl-3.5 text-base text-ae-text transition-colors focus:border-ae-blue focus:ring-2 focus:ring-ae-blue/30 focus:outline-none disabled:opacity-60";

/** Debug files shown as comparison tiles (everything else is a secondary link). */
const COMPARISON_DEBUG_FILES = {
  original: "original.jpg",
  mask: "mask.png",
  vehicle: "vehicle-transparent.png",
  background: "background.jpg",
} as const;

const COMPARISON_DEBUG_NAMES: ReadonlySet<string> = new Set(Object.values(COMPARISON_DEBUG_FILES));

/** Secondary debug files listed first, in this order. */
const SECONDARY_DEBUG_ORDER = ["shadow.png", "composite-before-shadow.jpg", "metadata.json"];

const DEBUG_ONLY_MESSAGE = "Nur mit PROCESSOR_DEBUG=true verfügbar";

const DEBUG_LABELS: Record<string, string> = {
  "original.jpg": "Original (normalisiert)",
  "mask.png": "Maske",
  "vehicle-transparent.png": "Fahrzeug freigestellt",
  "background.jpg": "Hintergrund",
  "composite-before-shadow.jpg": "Komposition ohne Schatten",
  "shadow.png": "Schatten",
  "final.jpg": "Ergebnis",
  "metadata.json": "Metadaten",
};

/** File types the browser may report without a MIME type (e.g. HEIC on some devices). */
const EXTENSION_TYPES: Record<string, string> = {
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
  heic: "image/heic",
  heif: "image/heif",
};

interface Selection {
  file: File;
  previewUrl: string;
}

type HealthState =
  | { status: "loading" }
  | { status: "ok"; health: ProcessorHealth }
  | { status: "error"; message: string };

type Phase = "idle" | "uploading" | "processing" | "done";

/* ------------------------------------------------------------------------ */
/* API helpers (same-origin proxy routes only)                               */
/* ------------------------------------------------------------------------ */

async function readApiError(response: Response, fallback: string): Promise<AppError> {
  let message: string | undefined;
  try {
    const body = (await response.json()) as Partial<ProcessorErrorBody>;
    if (typeof body.error?.message === "string") message = body.error.message;
  } catch {
    // not JSON – use the fallback
  }
  return new AppError(response.status === 404 ? "not_found" : "unknown", {
    userMessage: message ?? fallback,
  });
}

async function requestJson<T>(input: string, init: RequestInit, fallback: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(input, { ...init, cache: "no-store" });
  } catch (error) {
    if (init.signal?.aborted) throw error;
    throw new AppError("network", { cause: error });
  }
  if (!response.ok) throw await readApiError(response, fallback);
  try {
    return (await response.json()) as T;
  } catch (error) {
    throw new AppError("unknown", { userMessage: fallback, cause: error });
  }
}

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

async function loadHealth(signal: AbortSignal): Promise<HealthState> {
  try {
    const health = await requestJson<ProcessorHealth>(
      DEV_TEST_API.base,
      { signal },
      "Der Status des Bildverarbeitungs-Service konnte nicht abgerufen werden.",
    );
    return { status: "ok", health };
  } catch (error) {
    return { status: "error", message: toUserMessage(error) };
  }
}

/** Adds a MIME type from the file extension when the browser reports none. */
function withImageType(file: File): File {
  if (file.type) return file;
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  const type = EXTENSION_TYPES[extension];
  return type ? new File([file], file.name, { type, lastModified: file.lastModified }) : file;
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toLocaleString("de-DE", { maximumFractionDigits: 1 })} MB`;
  }
  return `${Math.max(1, Math.round(bytes / 1024)).toLocaleString("de-DE")} KB`;
}

function formatMs(ms: number): string {
  return ms >= 1000
    ? `${(ms / 1000).toLocaleString("de-DE", { maximumFractionDigits: 1 })} s`
    : `${Math.round(ms).toLocaleString("de-DE")} ms`;
}

function isImageName(name: string): boolean {
  return /\.(jpe?g|png|webp)$/.test(name);
}

/* ------------------------------------------------------------------------ */
/* Comparison (Original → Maske → Freigestellt → Showroom → Endergebnis)     */
/* ------------------------------------------------------------------------ */

interface ComparisonTile {
  key: string;
  caption: string;
  /** Shown image, or null → `message` placeholder. */
  image: { src: string; alt: string; source: string } | null;
  message: string;
  tone?: "muted" | "danger";
  /** Checkerboard behind the image so transparency is visible. */
  transparent?: boolean;
  highlight?: boolean;
}

/**
 * The five comparison tiles in their fixed order. Pure: image URLs that
 * failed to load (`failedImages`) fall back to the next source or a message.
 */
function buildComparisonTiles({
  job,
  previewUrl,
  previewFailed,
  failedImages,
  busy,
}: {
  job: ProcessorJob;
  previewUrl: string | null;
  previewFailed: boolean;
  failedImages: ReadonlySet<string>;
  busy: boolean;
}): ComparisonTile[] {
  const available = new Set(job.metadata.debugFiles.filter(isValidDebugFileName));
  const debugEnabled = available.size > 0;
  const missingMessage = debugEnabled ? "Datei nicht vorhanden." : DEBUG_ONLY_MESSAGE;
  const loadFailedMessage = "Bild konnte nicht geladen werden.";

  /** Debug file URL if listed and loadable, else null. */
  const debugUrl = (name: string): string | null => {
    if (!available.has(name)) return null;
    const url = DEV_TEST_API.debug(job.jobId, name);
    return failedImages.has(url) ? null : url;
  };
  const debugFailed = (name: string) =>
    available.has(name) && failedImages.has(DEV_TEST_API.debug(job.jobId, name));

  const debugTile = (
    key: string,
    caption: string,
    name: string,
    alt: string,
    transparent = false,
  ): ComparisonTile => {
    const src = debugUrl(name);
    return {
      key,
      caption,
      image: src ? { src, alt, source: name } : null,
      message: debugFailed(name) ? loadFailedMessage : missingMessage,
      tone: debugFailed(name) ? "danger" : "muted",
      transparent,
    };
  };

  // 1. Original: normalised debug original, else the local preview.
  const originalDebug = debugUrl(COMPARISON_DEBUG_FILES.original);
  const original: ComparisonTile = originalDebug
    ? {
        key: "original",
        caption: "Original",
        image: { src: originalDebug, alt: "Originalfoto (normalisiert)", source: COMPARISON_DEBUG_FILES.original },
        message: "",
      }
    : {
        key: "original",
        caption: "Original",
        image:
          previewUrl && !previewFailed && !failedImages.has(previewUrl)
            ? { src: previewUrl, alt: "Originalfoto", source: "Lokale Vorschau" }
            : null,
        message: previewUrl ? "Dieses Format kann der Browser nicht anzeigen." : "Kein Original vorhanden.",
      };

  // 4. Showroom: debug background, else the processor's showroom preview.
  const backgroundDebug = debugUrl(COMPARISON_DEBUG_FILES.background);
  const preset = isDevTestPresetId(job.preset) ? job.preset : DEFAULT_DEV_TEST_PRESET;
  const resultFile = job.result?.kind === "file" ? job.result : null;
  const showroomUrl = DEV_TEST_API.showroom(preset, showroomPreviewWidth(resultFile?.width));
  const showroomSrc = backgroundDebug ?? (failedImages.has(showroomUrl) ? null : showroomUrl);
  const showroom: ComparisonTile = {
    key: "showroom",
    caption: "Showroom ohne Fahrzeug",
    image: showroomSrc
      ? {
          src: showroomSrc,
          alt: "Showroom-Hintergrund ohne Fahrzeug",
          source: backgroundDebug ? COMPARISON_DEBUG_FILES.background : "Showroom-Vorschau",
        }
      : null,
    message: "Showroom konnte nicht geladen werden.",
    tone: "danger",
  };

  // 5. Final result.
  const resultUrl = DEV_TEST_API.result(job.jobId);
  const resultReady = job.status === "complete" && resultFile !== null && !failedImages.has(resultUrl);
  const result: ComparisonTile = {
    key: "result",
    caption: "Endergebnis",
    image: resultReady ? { src: resultUrl, alt: "Endergebnis", source: "Ergebnis" } : null,
    message:
      job.status === "complete" && resultFile
        ? "Das Ergebnis konnte nicht geladen werden."
        : job.result?.kind === "stored"
          ? "Ergebnis wurde im Speicher abgelegt."
          : job.status === "failed"
            ? "Bearbeitung fehlgeschlagen."
            : busy
              ? "Wird verarbeitet…"
              : "Kein Ergebnis vorhanden.",
    tone: job.status === "failed" || (job.status === "complete" && resultFile) ? "danger" : "muted",
    highlight: resultReady,
  };

  return [
    original,
    debugTile("mask", "Maske", COMPARISON_DEBUG_FILES.mask, "Maske der Freistellung"),
    debugTile(
      "vehicle",
      "Fahrzeug freigestellt",
      COMPARISON_DEBUG_FILES.vehicle,
      "Freigestelltes Fahrzeug",
      true,
    ),
    showroom,
    result,
  ];
}

/** Debug files that are not comparison tiles (shadow, composition, metadata, …). */
function secondaryDebugFiles(debugFiles: readonly string[]): string[] {
  const rest = debugFiles.filter((name) => !COMPARISON_DEBUG_NAMES.has(name));
  const rank = (name: string) => {
    const index = SECONDARY_DEBUG_ORDER.indexOf(name);
    return index === -1 ? SECONDARY_DEBUG_ORDER.length : index;
  };
  return [...rest].sort((a, b) => rank(a) - rank(b));
}

/* ------------------------------------------------------------------------ */
/* Screen                                                                    */
/* ------------------------------------------------------------------------ */

/**
 * Developer test page (`/dev/processing-test`): upload one photo, process it
 * with the Python processor and compare Original | Bearbeitet, plus the
 * intermediate steps (Original → Maske → Freigestellt → Showroom → Ergebnis).
 * Not linked from the app navigation.
 */
export function ProcessingTestScreen() {
  const exteriorShots = getOrderedShots(getShotTemplate()).filter((shot) => shot.category === "exterior");

  const [selection, setSelection] = useState<Selection | null>(null);
  const [previewFailed, setPreviewFailed] = useState(false);
  const [preset, setPreset] = useState<DevTestPresetId>(DEFAULT_DEV_TEST_PRESET);
  const [shotKey, setShotKey] = useState(DEFAULT_DEV_TEST_SHOT_KEY);
  const [phase, setPhase] = useState<Phase>("idle");
  const [job, setJob] = useState<ProcessorJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** Image URLs (result, debug files, showroom preview) that failed to load. */
  const [failedImages, setFailedImages] = useState<ReadonlySet<string>>(() => new Set());
  const [health, setHealth] = useState<HealthState>({ status: "loading" });

  const runRef = useRef<AbortController | null>(null);
  const resultsRef = useRef<HTMLElement>(null);

  const busy = phase === "uploading" || phase === "processing";
  const previewUrl = selection?.previewUrl ?? null;

  // Revoke the local preview URL when it is replaced or the screen unmounts.
  useEffect(() => {
    if (!previewUrl) return;
    return () => URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  // Stop polling when the screen goes away.
  useEffect(() => () => runRef.current?.abort(), []);

  // Service status (configured / reachable / debug mode).
  useEffect(() => {
    const controller = new AbortController();
    void loadHealth(controller.signal).then((next) => {
      if (!controller.signal.aborted) setHealth(next);
    });
    return () => controller.abort();
  }, []);

  /** Phones: bring progress / errors / comparison into view (side by side on desktop). */
  function revealResults() {
    if (window.matchMedia("(min-width: 1024px)").matches) return;
    requestAnimationFrame(() =>
      resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
    );
  }

  function resetRun() {
    runRef.current?.abort();
    runRef.current = null;
    setPhase("idle");
    setJob(null);
    setError(null);
    setFailedImages(new Set());
  }

  function markImageFailed(url: string) {
    setFailedImages((current) => (current.has(url) ? current : new Set(current).add(url)));
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const picked = event.target.files?.[0];
    event.target.value = ""; // allow choosing the same file again
    if (!picked) return;
    resetRun();
    const file = withImageType(picked);
    if (!file.type.toLowerCase().startsWith("image/")) {
      setSelection(null);
      setError("Bitte wählen Sie eine Bilddatei (z. B. JPG, PNG oder HEIC).");
      return;
    }
    if (file.size > DEV_TEST_MAX_UPLOAD_BYTES) {
      setSelection(null);
      setError(`Das Foto ist zu groß (maximal ${DEV_TEST_MAX_UPLOAD_MB} MB).`);
      return;
    }
    setPreviewFailed(false);
    setSelection({ file, previewUrl: URL.createObjectURL(file) });
  }

  async function handleProcess() {
    if (!selection) {
      setError("Bitte wählen Sie zuerst ein Fahrzeugfoto aus.");
      return;
    }
    runRef.current?.abort();
    const controller = new AbortController();
    const { signal } = controller;
    runRef.current = controller;
    setError(null);
    setJob(null);
    setFailedImages(new Set());
    setPhase("uploading");

    try {
      const body = new FormData();
      body.append("file", selection.file, selection.file.name);
      body.append("preset", preset);
      body.append("shotKey", shotKey);
      let current = await requestJson<ProcessorJob>(
        DEV_TEST_API.base,
        { method: "POST", body, signal },
        "Das Foto konnte nicht an den Bildverarbeitungs-Service übertragen werden.",
      );
      setJob(current);
      setPhase("processing");
      revealResults();

      const startedAt = Date.now();
      let pollErrors = 0;
      while (!isTerminalJobStatus(current.status)) {
        if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
          throw new AppError("unknown", {
            userMessage: "Die Bearbeitung dauert ungewöhnlich lange. Bitte später erneut versuchen.",
          });
        }
        await delay(POLL_INTERVAL_MS, signal);
        try {
          current = await requestJson<ProcessorJob>(
            DEV_TEST_API.job(current.jobId),
            { signal },
            "Der Bearbeitungsstatus konnte nicht abgerufen werden.",
          );
          pollErrors = 0;
          setJob(current);
        } catch (pollError) {
          if (signal.aborted) throw pollError;
          if (pollError instanceof AppError && pollError.code === "not_found") throw pollError;
          pollErrors += 1;
          if (pollErrors >= MAX_POLL_ERRORS) throw pollError;
        }
      }
      if (current.status === "failed") {
        setError(current.error ?? "Die Bearbeitung ist fehlgeschlagen.");
      }
    } catch (runError) {
      if (signal.aborted) return;
      setError(toUserMessage(runError, "Die Bearbeitung ist fehlgeschlagen."));
      revealResults();
    } finally {
      if (runRef.current === controller) {
        runRef.current = null;
        setPhase("done");
      }
    }
  }

  const resultFile = job?.status === "complete" && job.result?.kind === "file" ? job.result : null;
  const storedResult = job?.status === "complete" && job.result?.kind === "stored" ? job.result : null;
  const resultFailed = job !== null && failedImages.has(DEV_TEST_API.result(job.jobId));
  const debugFiles = job?.metadata.debugFiles.filter(isValidDebugFileName) ?? [];
  const timings = job ? Object.entries(job.metadata.timingsMs) : [];
  const showroomSource: ShowroomSource | null =
    job?.metadata.showroomSource ?? (health.status === "ok" ? health.health.showroomSource : null);
  // Debug files exist only once the job has ended (complete, or failed mid-way).
  const comparisonTiles =
    job && (job.status === "complete" || (job.status === "failed" && debugFiles.length > 0))
      ? buildComparisonTiles({ job, previewUrl, previewFailed, failedImages, busy })
      : null;

  return (
    <>
      <AppHeader />
      <PageContainer className="pb-16">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="text-2xl font-bold tracking-tight">Bildverarbeitung testen</h1>
          <span className="inline-flex items-center gap-1 rounded-full border border-ae-blue/40 bg-ae-blue-soft px-2 py-0.5 text-[11px] font-semibold text-[#5aa6ff]">
            <Bug className="size-3" aria-hidden />
            Entwicklung
          </span>
        </div>
        <p className="mt-1.5 max-w-2xl text-sm text-ae-muted">
          Ein Fahrzeugfoto wird an den Bildverarbeitungs-Service gesendet und im AutoExperten-Stil
          bearbeitet. Es wird nichts gespeichert – das Originalfoto bleibt unverändert.
        </p>

        <ServiceStatus state={health} />

        <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)] lg:items-start">
          {/* Form */}
          <section
            aria-label="Testauftrag"
            className="flex flex-col gap-5 rounded-2xl border border-ae-border bg-ae-surface p-4 shadow-[var(--shadow-card)] lg:sticky lg:top-20"
          >
            <Step number={1} label="Fahrzeugfoto auswählen" htmlFor="dev-file">
              <label
                htmlFor="dev-file"
                className={cn(
                  "flex cursor-pointer items-center gap-3 rounded-xl border border-dashed p-3 transition-colors",
                  "border-ae-border-strong bg-ae-surface-2 hover:border-ae-blue focus-within:border-ae-blue",
                  busy && "pointer-events-none opacity-60",
                )}
              >
                <span className="flex size-16 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-ae-surface-3 text-ae-muted">
                  {selection && !previewFailed ? (
                    <img
                      src={selection.previewUrl}
                      alt=""
                      className="size-full object-cover"
                      onError={() => setPreviewFailed(true)}
                    />
                  ) : (
                    <ImageUp className="size-6" aria-hidden />
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  {selection ? (
                    <>
                      <span className="block truncate text-sm font-semibold">{selection.file.name}</span>
                      <span className="block text-xs text-ae-muted">
                        {formatBytes(selection.file.size)}
                        {previewFailed ? " · keine Vorschau im Browser" : ""}
                      </span>
                      <span className="mt-0.5 block text-xs font-semibold text-ae-blue">Anderes Foto wählen</span>
                    </>
                  ) : (
                    <>
                      <span className="block text-sm font-semibold">Foto auswählen</span>
                      <span className="block text-xs text-ae-muted">
                        JPG, PNG oder HEIC · maximal {DEV_TEST_MAX_UPLOAD_MB} MB
                      </span>
                    </>
                  )}
                </span>
                <input
                  id="dev-file"
                  type="file"
                  accept="image/*"
                  className="sr-only"
                  onChange={handleFileChange}
                  disabled={busy}
                />
              </label>
            </Step>

            <Step number={2} label="Bearbeitungsstil" htmlFor="dev-preset">
              <select
                id="dev-preset"
                className={SELECT_CLASSES}
                value={preset}
                disabled={busy}
                onChange={(event) => {
                  if (isDevTestPresetId(event.target.value)) setPreset(event.target.value);
                }}
              >
                {DEV_TEST_PRESETS.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </Step>

            <Step number={3} label="Aufnahmeposition" htmlFor="dev-shot">
              <select
                id="dev-shot"
                className={SELECT_CLASSES}
                value={shotKey}
                disabled={busy}
                onChange={(event) => setShotKey(event.target.value)}
              >
                {exteriorShots.map((shot) => (
                  <option key={shot.key} value={shot.key}>
                    {shot.title}
                  </option>
                ))}
              </select>
            </Step>

            <LoadingButton
              size="lg"
              fullWidth
              loading={busy}
              loadingText={phase === "uploading" ? "Wird hochgeladen…" : "Wird verarbeitet…"}
              disabled={!selection}
              onClick={() => void handleProcess()}
              icon={<WandSparkles className="size-5" aria-hidden />}
            >
              Verarbeiten
            </LoadingButton>

            <p className="-mt-2 flex gap-2 text-xs text-ae-subtle">
              <ShieldCheck className="size-4 shrink-0 text-ae-success" aria-hidden />
              Das Fahrzeug wird nie künstlich erzeugt oder verändert – nur der Hintergrund, Schatten und
              Licht um das Fahrzeug.
            </p>
          </section>

          {/* Results */}
          <section
            ref={resultsRef}
            aria-label="Ergebnis"
            aria-live="polite"
            className="flex min-w-0 scroll-mt-20 flex-col gap-4"
          >
            {(busy || job) && <JobStatusCard phase={phase} job={job} />}

            {error && (
              <div
                role="alert"
                className="flex gap-3 rounded-xl border border-ae-danger/35 bg-ae-danger/8 p-4 text-sm"
              >
                <TriangleAlert className="size-5 shrink-0 text-ae-danger" aria-hidden />
                <p>{error}</p>
              </div>
            )}

            {job && selection && (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <ImagePanel title="Original">
                  {previewFailed ? (
                    <PanelMessage>Dieses Format kann der Browser nicht anzeigen.</PanelMessage>
                  ) : (
                    <img src={selection.previewUrl} alt="Originalfoto" className="size-full object-contain" />
                  )}
                </ImagePanel>
                <ImagePanel title="Bearbeitet" highlight={Boolean(resultFile)}>
                  {resultFile && !resultFailed ? (
                    <a href={DEV_TEST_API.result(job.jobId)} target="_blank" rel="noreferrer" className="block size-full">
                      <img
                        src={DEV_TEST_API.result(job.jobId)}
                        alt="Bearbeitetes Foto"
                        className="size-full object-contain"
                        onError={() => markImageFailed(DEV_TEST_API.result(job.jobId))}
                      />
                    </a>
                  ) : resultFailed ? (
                    <PanelMessage tone="danger">Das Ergebnis konnte nicht geladen werden.</PanelMessage>
                  ) : storedResult ? (
                    <PanelMessage>
                      Ergebnis wurde im Speicher abgelegt:{" "}
                      <span className="break-all font-mono text-xs">{storedResult.processedStoragePath}</span>
                    </PanelMessage>
                  ) : job.status === "failed" ? (
                    <PanelMessage tone="danger">Bearbeitung fehlgeschlagen.</PanelMessage>
                  ) : busy ? (
                    <PanelMessage>
                      <Spinner className="mx-auto mb-2 block size-6 text-ae-blue" />
                      Wird verarbeitet…
                    </PanelMessage>
                  ) : (
                    <PanelMessage>Kein Ergebnis vorhanden.</PanelMessage>
                  )}
                </ImagePanel>
              </div>
            )}

            {resultFile && !resultFailed && job && (
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <a
                  href={DEV_TEST_API.result(job.jobId, true)}
                  download={`AE_test_${job.jobId}.jpg`}
                  className={buttonClasses({ size: "lg", className: "w-full sm:w-auto" })}
                >
                  <Download className="size-5" aria-hidden />
                  Ergebnis herunterladen
                </a>
                {resultFile.width > 0 && (
                  <p className="text-center text-xs text-ae-muted tabular-nums sm:text-right">
                    {resultFile.width} × {resultFile.height} px
                    {resultFile.bytes > 0 ? ` · ${formatBytes(resultFile.bytes)}` : ""}
                  </p>
                )}
              </div>
            )}

            {job && showroomSource && <ShowroomStatus source={showroomSource} />}

            {job && job.warnings.length > 0 && (
              <Notice tone="warning" icon={<TriangleAlert className="size-5 shrink-0 text-ae-warning" aria-hidden />}>
                <span className="font-semibold text-ae-text">Hinweise</span>
                <ul className="mt-1 list-disc space-y-0.5 pl-4">
                  {job.warnings.map((warning, index) => (
                    <li key={`${warning.code}-${index}`}>{warning.message}</li>
                  ))}
                </ul>
              </Notice>
            )}

            {job && (job.metadata.segmenter || timings.length > 0) && (
              <details className="group rounded-xl border border-ae-border bg-ae-surface text-sm">
                <summary className="cursor-pointer list-none px-4 py-3 font-semibold marker:hidden">
                  Technische Details
                  <span className="ml-1 text-ae-subtle group-open:hidden">anzeigen</span>
                </summary>
                <div className="border-t border-ae-border px-4 py-3">
                  <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-xs">
                    <dt className="text-ae-muted">Auftrag</dt>
                    <dd className="break-all font-mono">{job.jobId}</dd>
                    {job.metadata.shotKind && (
                      <>
                        <dt className="text-ae-muted">Aufnahmeart</dt>
                        <dd>{job.metadata.shotKind}</dd>
                      </>
                    )}
                    {job.metadata.segmenter && (
                      <>
                        <dt className="text-ae-muted">Freistellung</dt>
                        <dd className="break-all">
                          {job.metadata.segmenter}
                          {job.metadata.model ? ` · ${job.metadata.model}` : ""}
                        </dd>
                      </>
                    )}
                    {timings.map(([step, ms]) => (
                      <TimingRow key={step} step={step} ms={ms} />
                    ))}
                  </dl>
                  {(job.metadata.placement || job.metadata.adjustments) && (
                    <pre className="mt-3 max-h-64 overflow-auto rounded-lg bg-ae-bg p-3 text-[11px] leading-relaxed text-ae-muted">
                      {JSON.stringify(
                        { placement: job.metadata.placement, adjustments: job.metadata.adjustments },
                        null,
                        2,
                      )}
                    </pre>
                  )}
                </div>
              </details>
            )}

            {!job && !busy && !error && (
              <div className="flex flex-col items-center rounded-2xl border border-dashed border-ae-border-strong bg-ae-surface/60 px-6 py-10 text-center">
                <div className="mb-3 flex size-12 items-center justify-center rounded-2xl bg-ae-blue-soft text-ae-blue">
                  <ImageUp className="size-6" aria-hidden />
                </div>
                <p className="text-sm font-semibold">Noch kein Testauftrag</p>
                <p className="mt-1 max-w-sm text-sm text-ae-muted">
                  Foto auswählen und „Verarbeiten“ tippen. Original und Ergebnis erscheinen hier
                  nebeneinander.
                </p>
              </div>
            )}
          </section>
        </div>

        {job && comparisonTiles && (
          <ComparisonSection
            tiles={comparisonTiles}
            secondaryFiles={secondaryDebugFiles(debugFiles).map((name) => ({
              name,
              url: DEV_TEST_API.debug(job.jobId, name),
            }))}
            onImageError={markImageFailed}
          />
        )}
      </PageContainer>
    </>
  );
}

/* ------------------------------------------------------------------------ */
/* Building blocks                                                           */
/* ------------------------------------------------------------------------ */

function Step({
  number,
  label,
  htmlFor,
  children,
}: {
  number: number;
  label: string;
  htmlFor: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={htmlFor} className="flex items-center gap-2 text-sm font-medium text-ae-muted">
        <span className="flex size-5 items-center justify-center rounded-full bg-ae-surface-3 text-[11px] font-bold text-ae-text">
          {number}
        </span>
        {label}
      </label>
      {children}
    </div>
  );
}

function ServiceStatus({ state }: { state: HealthState }) {
  if (state.status === "loading") {
    return (
      <p className="mt-4 flex items-center gap-2 text-xs text-ae-muted" role="status">
        <Spinner className="size-4" />
        Bildverarbeitungs-Service wird geprüft…
      </p>
    );
  }
  if (state.status === "error") {
    return (
      <div role="alert" className="mt-4 flex gap-3 rounded-xl border border-ae-warning/35 bg-ae-warning/10 p-3 text-sm">
        <ServerCrash className="size-5 shrink-0 text-ae-warning" aria-hidden />
        <p>{state.message}</p>
      </div>
    );
  }
  const { health } = state;
  const details = [
    health.version && `Version ${health.version}`,
    health.model && `Modell ${health.model}`,
    `Debug ${health.debug ? "an" : "aus"}`,
  ].filter(Boolean);
  return (
    <div className="mt-4 flex items-start gap-2 text-xs">
      <span className="mt-1 size-2 shrink-0 rounded-full bg-ae-success" aria-hidden />
      <p className="min-w-0">
        <span className="font-semibold text-ae-success">Service verbunden</span>
        <span className="block text-ae-muted">
          {details.join(" · ")}
          {health.showroomSource && (
            <>
              {" · "}
              <span className={cn(health.showroomSource === "fallback" && "font-semibold text-ae-warning")}>
                {health.showroomSource === "master" ? "Showroom: finales Master-Foto" : "Showroom: Fallback"}
              </span>
            </>
          )}
        </span>
      </p>
    </div>
  );
}

/** Which showroom the job was composited onto (fallback → clear amber notice). */
function ShowroomStatus({ source }: { source: ShowroomSource }) {
  if (source === "master") {
    return (
      <p>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-ae-border-strong bg-ae-surface-2 px-2.5 py-0.5 text-xs font-semibold text-ae-muted">
          <span className="size-1.5 rounded-full bg-current" aria-hidden />
          Showroom: finales Master-Foto
        </span>
      </p>
    );
  }
  return (
    <div className="flex gap-3 rounded-xl border border-ae-warning/45 bg-ae-warning/10 p-4 text-sm text-ae-text">
      <TriangleAlert className="size-5 shrink-0 text-ae-warning" aria-hidden />
      <p className="min-w-0">
        <strong className="font-semibold text-ae-warning">Fallback-Showroom aktiv</strong> – das finale
        AutoExperten-Showroom-Foto fehlt. Bitte die Datei{" "}
        <code className="rounded bg-ae-bg/60 px-1 py-px text-[13px] [overflow-wrap:anywhere]">
          public/<wbr />presets/<wbr />autoexperten-standard-showroom.jpg
        </code>{" "}
        ablegen.
      </p>
    </div>
  );
}

function JobStatusCard({ phase, job }: { phase: Phase; job: ProcessorJob | null }) {
  const status = job?.status;
  const label =
    phase === "uploading" || !status ? "Wird hochgeladen…" : PROCESSING_JOB_STATUS_LABELS[status];
  const progress = status === "complete" ? 1 : (job?.progress ?? 0);
  return (
    <div className="rounded-2xl border border-ae-border bg-ae-surface p-4">
      <div className={cn("flex items-center justify-between gap-3 text-sm", status !== "failed" && "mb-2")}>
        <span
          className={cn(
            "flex items-center gap-2 font-semibold",
            status === "complete" && "text-ae-success",
            status === "failed" && "text-ae-danger",
          )}
        >
          {status === "complete" ? (
            <CircleCheck className="size-4" aria-hidden />
          ) : status === "failed" ? (
            <TriangleAlert className="size-4" aria-hidden />
          ) : (
            <Spinner className="size-4 text-ae-blue" />
          )}
          {label}
        </span>
        {status !== "failed" && (
          <span className="text-ae-muted tabular-nums">{Math.round(progress * 100)} %</span>
        )}
      </div>
      {status !== "failed" && (
        <ProgressBar
          value={Math.round(progress * 100)}
          max={100}
          label="Bearbeitungsfortschritt"
          tone={status === "complete" ? "success" : "blue"}
        />
      )}
    </div>
  );
}

function ImagePanel({
  title,
  highlight = false,
  children,
}: {
  title: string;
  highlight?: boolean;
  children: ReactNode;
}) {
  return (
    <figure className="min-w-0">
      <figcaption className="mb-1.5 text-xs font-semibold tracking-wide text-ae-muted uppercase">{title}</figcaption>
      <div
        className={cn(
          "flex aspect-[4/3] items-center justify-center overflow-hidden rounded-xl border bg-ae-bg",
          highlight ? "border-ae-blue/50" : "border-ae-border",
        )}
      >
        {children}
      </div>
    </figure>
  );
}

function PanelMessage({ children, tone = "muted" }: { children: ReactNode; tone?: "muted" | "danger" }) {
  return (
    <div className={cn("px-4 text-center text-sm", tone === "danger" ? "text-ae-danger" : "text-ae-muted")}>
      {children}
    </div>
  );
}

function Notice({
  tone,
  icon,
  children,
}: {
  tone: "info" | "warning";
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex gap-3 rounded-xl border p-4 text-sm text-ae-muted",
        tone === "info" ? "border-ae-blue/30 bg-ae-blue-soft" : "border-ae-warning/35 bg-ae-warning/10",
      )}
    >
      {icon}
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function TimingRow({ step, ms }: { step: string; ms: number }) {
  return (
    <>
      <dt className="text-ae-muted">{step === "total" ? "Gesamtzeit" : step}</dt>
      <dd className="tabular-nums">{formatMs(ms)}</dd>
    </>
  );
}

function ComparisonSection({
  tiles,
  secondaryFiles,
  onImageError,
}: {
  tiles: ComparisonTile[];
  secondaryFiles: Array<{ name: string; url: string }>;
  onImageError: (url: string) => void;
}) {
  return (
    <section aria-labelledby="comparison-title" className="mt-8">
      <h2 id="comparison-title" className="text-lg font-semibold tracking-tight">
        Vergleich der Bearbeitungsschritte
      </h2>
      <p className="mt-0.5 text-sm text-ae-muted">
        Bild antippen, um es in voller Größe in einem neuen Tab zu öffnen.
        <span className="sm:hidden"> Seitlich wischen für alle fünf Bilder.</span>
      </p>
      {/* Phones: swipeable strip (bleeds to the screen edge); larger screens: grid. */}
      <ol className="-mx-4 mt-3 flex snap-x snap-mandatory scroll-px-4 gap-3 overflow-x-auto px-4 pb-2 sm:mx-0 sm:grid sm:grid-cols-3 sm:overflow-visible sm:px-0 sm:pb-0 xl:grid-cols-5">
        {tiles.map((tile) => (
          <li key={tile.key} className="w-[78%] shrink-0 snap-start sm:w-auto">
            <ComparisonTileView tile={tile} onImageError={onImageError} />
          </li>
        ))}
      </ol>

      {secondaryFiles.length > 0 && (
        <div className="mt-4">
          <h3 className="mb-2 text-xs font-semibold text-ae-muted">Weitere Debug-Dateien</h3>
          <ul className="flex flex-wrap gap-2">
            {secondaryFiles.map((file) => (
              <li key={file.name} className="min-w-0 max-w-full">
                <a
                  href={file.url}
                  target="_blank"
                  rel="noreferrer"
                  title={file.name}
                  className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-ae-border bg-ae-surface px-2.5 py-1.5 text-xs transition-colors hover:border-ae-border-strong"
                >
                  {isImageName(file.name) ? (
                    <ImageIcon className="size-3.5 shrink-0 text-ae-muted" aria-hidden />
                  ) : (
                    <FileJson className="size-3.5 shrink-0 text-ae-muted" aria-hidden />
                  )}
                  <span className="whitespace-nowrap font-semibold">{DEBUG_LABELS[file.name] ?? file.name}</span>
                  <span className="min-w-0 truncate font-mono text-[11px] text-ae-subtle">{file.name}</span>
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function ComparisonTileView({
  tile,
  onImageError,
}: {
  tile: ComparisonTile;
  onImageError: (url: string) => void;
}) {
  const { image } = tile;
  return (
    <figure className="min-w-0">
      <figcaption className="mb-1.5 truncate text-xs font-semibold tracking-wide text-ae-muted uppercase">
        {tile.caption}
      </figcaption>
      <div
        className={cn(
          "relative aspect-[4/3] overflow-hidden rounded-xl border bg-ae-bg",
          tile.highlight ? "border-ae-blue/50" : "border-ae-border",
          // Light/dark checkerboard so the transparent cut-out is readable.
          image &&
            tile.transparent &&
            "bg-[conic-gradient(#d5d9df_25%,#9aa1ab_0_50%,#d5d9df_0_75%,#9aa1ab_0)] bg-[length:16px_16px]",
        )}
      >
        {image ? (
          <a
            href={image.src}
            target="_blank"
            rel="noreferrer"
            aria-label={`${tile.caption} in voller Größe öffnen`}
            className="group block size-full"
          >
            <img
              key={image.src}
              src={image.src}
              alt={image.alt}
              decoding="async"
              className="size-full object-contain"
              onError={() => onImageError(image.src)}
            />
            <span
              className="absolute right-1.5 bottom-1.5 flex size-7 items-center justify-center rounded-lg bg-black/55 text-white opacity-80 transition-opacity group-hover:opacity-100"
              aria-hidden
            >
              <Maximize2 className="size-3.5" />
            </span>
          </a>
        ) : (
          <div
            className={cn(
              "flex size-full flex-col items-center justify-center gap-2 px-3 text-center text-xs",
              tile.tone === "danger" ? "text-ae-danger" : "text-ae-subtle",
            )}
          >
            {tile.message === DEBUG_ONLY_MESSAGE && <EyeOff className="size-5" aria-hidden />}
            <span>{tile.message}</span>
          </div>
        )}
      </div>
      <p className="mt-1 truncate font-mono text-[11px] text-ae-subtle">{image ? image.source : "\u00a0"}</p>
    </figure>
  );
}
