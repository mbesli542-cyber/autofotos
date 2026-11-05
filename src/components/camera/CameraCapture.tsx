"use client";

import { ArrowLeft, CloudOff, LayoutGrid, RotateCcw, ShieldOff } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import { getSlotImageUrl } from "@/components/photos/ShotThumbnail";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { useCameraStream } from "@/hooks/use-camera-stream";
import { useShotCheckStates } from "@/hooks/use-photo-checks";
import { usePendingUploads } from "@/hooks/use-upload-queue";
import { getAppServices } from "@/lib/app-services";
import { cameraReturnHref, isEntryForPage, type CameraReturnTarget } from "@/lib/camera/camera-links";
import { captureStill } from "@/lib/camera/capture";
import { CAMERA_CONFIG } from "@/lib/camera/config";
import { checkPhotoFile, PHOTO_FILE_ACCEPT } from "@/lib/camera/photo-file";
import { noopQualityChecker, type CaptureQualityWarning } from "@/lib/camera/quality";
import type { Vehicle, VehiclePhotoWithUrls } from "@/lib/domain/types";
import { isOlderThanSavedPhoto } from "@/lib/photos/capture-order";
import { buildPhotoSlots, isSlotCaptured, type PhotoSlot } from "@/lib/photos/photo-slots";
import { storeAccessCode } from "@/lib/processing/access-code-storage";
import { photoChecks, type PhotoCheck } from "@/lib/processing/photo-checks";
import {
  autoCheckOffMessage,
  checkResultMessage,
  getPhotoCheckState,
  isPassThroughCategory,
  passThroughState,
  retakesLeftMessage,
} from "@/lib/processing/shot-check";
import {
  getAdjacentShotKey,
  getFirstMissingShotKey,
  getNextRetakeShotKey,
  getNextShotKeyAfterCapture,
} from "@/lib/shots/shot-progress";
import { getOrderedShots, getShot, type ShotDefinition, type ShotTemplate } from "@/lib/shots/shot-template";
import { AutoCheckInfo } from "./AutoCheckInfo";
import { CameraErrorPanel } from "./CameraErrorPanel";
import { CameraOverlay } from "./CameraOverlay";
import { CapturePreview } from "./CapturePreview";
import { CaptureControls } from "./CaptureControls";
import { CheckStatusDetails, CheckStatusLine, checkStatusContent, type CheckBannerState } from "./CheckBanner";
import { InstructionPanel } from "./InstructionPanel";
import { ShotProgress } from "./ShotProgress";

interface PreviewState {
  url: string;
  shot: ShotDefinition;
  warnings: CaptureQualityWarning[];
  nextKey: string | null;
}

type Router = ReturnType<typeof useRouter>;

/** The parts of the Navigation API used here (not in every browser yet). */
interface SessionHistory {
  currentEntry: NavigationHistoryEntry | null;
  entries(): NavigationHistoryEntry[];
}

/** URL of the history entry before the current one – null where the browser cannot tell. */
function previousHistoryEntryUrl(): string | null {
  const history = (window as Window & { navigation?: SessionHistory }).navigation;
  const index = history?.currentEntry?.index ?? 0;
  return index > 0 ? (history?.entries()[index - 1]?.url ?? null) : null;
}

/** Whether the camera was opened from the page `href` leads to. */
function isPreviousPage(href: string): boolean {
  return isEntryForPage(previousHistoryEntryUrl(), href, window.location.href);
}

/**
 * Leaves the camera without leaving it in the history behind the page it
 * returns to: back to that page when the camera was opened from there (no
 * second entry of it), otherwise that page replaces the camera's entry.
 */
function leaveCamera(router: Router, href: string) {
  if (isPreviousPage(href)) router.back();
  else router.replace(href);
}

/** How long the banner of a finished check stays (ms): a rejection longer than an OK. */
const CHECK_BANNER_MS = { ok: 3_500, hint: 6_000, retake: 9_000, error: 6_000 } as const;

/**
 * The banner for a finished check of the camera's vehicle – null when the
 * check is outdated: a newer capture of its shot is on its way or saved.
 */
function bannerFor(check: PhotoCheck, slots: readonly PhotoSlot[]): CheckBannerState | null {
  const slot = slots.find((entry) => entry.key === check.shotKey);
  if (!slot) return null;
  if (slot.pending && slot.pending.id !== check.photoId) return null;
  if (slot.photo && slot.photo.id !== check.photoId && isOlderThanSavedPhoto(check.takenAt, slot.photo)) return null;
  // Interior and detail shots are never checked: nothing to report unless the file was refused.
  const checked = getPhotoCheckState(check);
  const state = isPassThroughCategory(slot.category) ? passThroughState(checked, true) : checked;
  const text = checkResultMessage(slot, state);
  if (!text || (state.kind !== "ok" && state.kind !== "retake" && state.kind !== "error")) return null;
  // A result with a processor hint is not a plain "passt": amber, like its segment.
  const kind = state.kind === "ok" && state.hint ? "hint" : state.kind;
  return { id: `${check.photoId}-${check.status}`, shotKey: slot.key, kind, text };
}

const UNKNOWN_SHOT_HINT = "Diese Aufnahme gibt es in der Kamera nicht. Es geht mit den Pflichtfotos weiter.";
const CHOOSE_SHOT_HINT = "Tippen Sie oben auf das Foto, das Sie wiederholen möchten.";

/**
 * Guided capture: shows the current shot, its framing guide and progress,
 * captures a full-resolution photo, stores it (offline-safe queue) and
 * automatically advances to the next missing shot – or, with `returnTo`
 * (retake of one shot from "Fotos überprüfen" / "Fotos bearbeiten"), goes
 * back there. The browser's Back never reopens the camera after it was left
 * (see `leaveCamera`). It never picks a photo to replace by itself: a retake
 * is a requested shot (?shot=) or one the employee chose.
 */
export function CameraCapture({
  vehicle,
  photos,
  template,
  initialShotKey,
  returnTo,
}: {
  vehicle: Vehicle;
  photos: readonly VehiclePhotoWithUrls[];
  template: ShotTemplate;
  initialShotKey: string | null;
  returnTo: CameraReturnTarget | null;
}) {
  const router = useRouter();
  const toast = useToast();
  const videoRef = useRef<HTMLVideoElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  /** False while the camera route is hidden (see the layout effect below). */
  const shownRef = useRef(false);
  /** The preview on screen, for leaving the camera (see the layout effect below). */
  const previewRef = useRef<PreviewState | null>(null);
  const camera = useCameraStream(videoRef);
  const pending = usePendingUploads(vehicle.id);

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [flashKey, setFlashKey] = useState(0);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  /** Result of the last finished automatic check (shown briefly). */
  const [banner, setBanner] = useState<CheckBannerState | null>(null);
  /** The check status line whose full text is open (CheckStatusContent.id). */
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [showCheckInfo, setShowCheckInfo] = useState(false);

  const reviewHref = cameraReturnHref(vehicle.id, "fotos");
  /** Close button / after a single retake: back to where the retake was started. */
  const exitHref = cameraReturnHref(vehicle.id, returnTo);
  const shots = useMemo(() => getOrderedShots(template).filter((s) => s.required), [template]);
  const slots = useMemo(() => buildPhotoSlots(template, photos, pending).required, [template, photos, pending]);
  const { states: checkStates, snapshot: checkSnapshot } = useShotCheckStates(vehicle.id, slots);
  /** The slots as shown, for the check listener (see below). */
  const slotsRef = useRef(slots);
  const capturedKeys = useMemo(
    () => new Set(slots.filter(isSlotCaptured).map((slot) => slot.key)),
    [slots],
  );

  // Only a required shot can be requested (?shot=…). Anything else – e.g. an
  // additional photo – never selects a shot by itself: the guided order
  // applies and a hint says so.
  const requestedKey = shots.some((s) => s.key === initialShotKey) ? initialShotKey : null;
  const unknownShotRequested = Boolean(initialShotKey) && requestedKey === null;
  const firstMissingKey = getFirstMissingShotKey(template, capturedKeys);
  const fallbackKey = requestedKey ?? firstMissingKey ?? shots[0]?.key ?? "";
  const currentKey = selectedKey ?? fallbackKey;
  // Every required photo is there and no shot was requested: nothing is
  // captured until the employee chooses the photo to replace.
  const mustChooseShot = selectedKey === null && requestedKey === null && firstMissingKey === null;
  const shot = getShot(template, currentKey) ?? shots[0];
  const currentIndex = shots.findIndex((s) => s.key === shot?.key);
  const currentSlot = slots[currentIndex];
  const previousKey = shot ? getAdjacentShotKey(template, shot.key, -1) : null;
  const previousSlot = slots.find((slot) => slot.key === previousKey) ?? null;
  const nextKeyInOrder = shot ? getAdjacentShotKey(template, shot.key, 1) : null;

  const failedItems = pending.filter((item) => item.status === "failed");
  /** Failed uploads a retry can still save. */
  const failedCount = failedItems.filter((item) => item.retryable).length;
  /** Files the server refused for good – only "Verwerfen" (large view in "Fotos überprüfen") helps. */
  const refusedCount = failedItems.length - failedCount;
  const savingCount = pending.length - failedItems.length;
  const capturedCount = capturedKeys.size;
  const remainingCount = shots.length - capturedCount;
  /** Shots the automatic check rejected (template order). */
  const retakeKeys = useMemo(
    () => slots.filter((slot) => checkStates.get(slot.key)?.kind === "retake").map((slot) => slot.key),
    [slots, checkStates],
  );
  /** The rejected shots as shown, for finishing a preview (see completePreview). */
  const retakeKeysRef = useRef(retakeKeys);
  const checkOffMessage = autoCheckOffMessage(checkSnapshot.availability);

  const completePreview = useCallback(
    (state: PreviewState) => {
      setPreview(null);
      if (returnTo) {
        leaveCamera(router, exitHref);
        return;
      }
      if (state.nextKey) {
        setSelectedKey(state.nextKey);
        return;
      }
      // No shot is missing: on to the next shot the check rejected – the camera
      // is left only when none is red any more.
      const retakes = retakeKeysRef.current.filter((key) => key !== state.shot.key);
      const nextRetake = getNextRetakeShotKey(template, retakes, state.shot.key);
      if (nextRetake) {
        toast.show(retakesLeftMessage(retakes.length));
        setSelectedKey(nextRetake);
        return;
      }
      toast.success("Alle Pflichtfotos aufgenommen. Bitte prüfen Sie die Fotos.");
      leaveCamera(router, reviewHref);
    },
    [returnTo, exitHref, reviewHref, router, toast, template],
  );

  // Show the captured photo briefly, then advance automatically.
  useEffect(() => {
    if (!preview || preview.warnings.length > 0) return;
    const timer = setTimeout(() => completePreview(preview), CAMERA_CONFIG.capturePreviewMs);
    return () => clearTimeout(timer);
  }, [preview, completePreview]);

  // The preview's object URL lives exactly as long as the preview.
  useEffect(() => {
    if (!preview) return;
    return () => URL.revokeObjectURL(preview.url);
  }, [preview]);

  useLayoutEffect(() => {
    previewRef.current = preview;
  }, [preview]);

  useLayoutEffect(() => {
    slotsRef.current = slots;
  }, [slots]);

  useLayoutEffect(() => {
    retakeKeysRef.current = retakeKeys;
  }, [retakeKeys]);

  // A finished automatic check of this vehicle shows a short banner (while the camera is shown).
  useEffect(
    () =>
      photoChecks.onSettled((check) => {
        if (check.vehicleId !== vehicle.id || !shownRef.current) return;
        const next = bannerFor(check, slotsRef.current);
        if (next) setBanner(next);
      }),
    [vehicle.id],
  );

  // The banner stays while its full text is open.
  const bannerOpen = banner !== null && expandedId === `banner:${banner.id}`;
  useEffect(() => {
    if (!banner || bannerOpen) return;
    const timer = setTimeout(() => setBanner(null), CHECK_BANNER_MS[banner.kind]);
    return () => clearTimeout(timer);
  }, [banner, bannerOpen]);

  // Routes stay mounted (React Activity): the browser's Back shows this camera
  // again as it was left (a new visit gets a fresh camera, see CameraScreen).
  // A preview still shown when the camera is left is finished without
  // navigating – its photo is saved – so it neither reappears nor leaves the
  // camera by itself, and the guided order continues with the next shot.
  useLayoutEffect(() => {
    shownRef.current = true;
    return () => {
      shownRef.current = false;
      const left = previewRef.current;
      if (left?.nextKey && !returnTo) setSelectedKey(left.nextKey);
      setPreview(null);
      setFlashKey(0);
      setBanner(null);
      setExpandedId(null);
      setShowCheckInfo(false);
    };
  }, [returnTo]);

  // Toasts appear in the middle of the viewfinder while the camera is shown,
  // so they never cover the current shot, the progress or the shutter.
  useLayoutEffect(() => {
    const root = document.documentElement;
    root.dataset.cameraOpen = "";
    return () => {
      delete root.dataset.cameraOpen;
    };
  }, []);

  async function storeCapture(file: Blob, target: ShotDefinition) {
    const { uploads } = getAppServices();
    // Keep this shot selected while it is saved and previewed (the background
    // upload may otherwise change the auto-selected "first missing" shot).
    setSelectedKey(target.key);
    try {
      // Persisted locally first – never lost if the upload fails.
      await uploads.enqueue({
        vehicleId: vehicle.id,
        shotKey: target.key,
        shotOrder: target.order,
        title: target.title,
        file,
        // The real capture time ("Aufgenommen am"); which photo of the shot is
        // the current one follows src/lib/photos/capture-order.ts.
        takenAt: new Date().toISOString(),
      });
    } catch {
      toast.error("Foto konnte nicht gespeichert werden. Bitte versuchen Sie es erneut.");
      return;
    }
    const nextKey = getNextShotKeyAfterCapture(template, new Set([...capturedKeys, target.key]), target.key);
    const quality = shownRef.current ? await noopQualityChecker.analyzeCapture(file, target) : null;
    if (!quality || !shownRef.current) {
      // The camera was left meanwhile: the photo is saved, there is nothing to preview.
      if (nextKey && !returnTo) setSelectedKey(nextKey);
      return;
    }
    setPreview({ url: URL.createObjectURL(file), shot: target, warnings: quality.warnings, nextKey });
  }

  function openFilePicker() {
    if (mustChooseShot) {
      toast.error(CHOOSE_SHOT_HINT);
      return;
    }
    fileInputRef.current?.click();
  }

  async function handleShutter() {
    const video = videoRef.current;
    if (!video || !shot || busy || preview || mustChooseShot || camera.state.status !== "ready") return;
    setBusy(true);
    setFlashKey((key) => key + 1);
    try {
      const blob = await captureStill(video, camera.trackRef.current);
      await storeCapture(blob, shot);
    } catch {
      toast.error("Foto konnte nicht aufgenommen werden. Bitte versuchen Sie es erneut.");
    } finally {
      setBusy(false);
    }
  }

  async function handleFileSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !shot || mustChooseShot) return;
    const checked = checkPhotoFile(file);
    if (!checked.ok) {
      toast.error(checked.message);
      return;
    }
    setBusy(true);
    try {
      // Uploaded files are stored unchanged (original quality and EXIF).
      await storeCapture(checked.file, shot);
    } finally {
      setBusy(false);
    }
  }

  function goTo(key: string | null) {
    if (key) setSelectedKey(key);
  }

  /** A shot chosen in the progress, banner or retake hint: jump there (the banner closes, so its state shows). */
  function selectShot(key: string) {
    setBanner(null);
    setExpandedId(null);
    goTo(key);
  }

  /** Access code entered in the camera (demo mode): stored on this device, the photos taken so far are checked. */
  function handleAccessCode(code: string) {
    storeAccessCode(code);
    setShowCheckInfo(false);
    void photoChecks.checkVehicle(vehicle.id);
  }

  if (!shot || !currentSlot) return null;

  const streamReady = camera.state.status === "ready";
  const ratio = streamReady && camera.state.status === "ready" ? camera.state.width / camera.state.height : 4 / 3;
  const isPortraitStream = streamReady && ratio < 1;
  const alreadyCaptured = isSlotCaptured(currentSlot);
  const currentCheck = checkStates.get(shot.key) ?? { kind: "none" as const };
  /** The check status line in the header: a check that just finished, else the current shot's state. */
  const statusContent = preview ? null : checkStatusContent(banner, shot, currentCheck, alreadyCaptured);
  const statusExpanded = statusContent !== null && expandedId === statusContent.id;
  const shotHint =
    selectedKey !== null || preview || busy
      ? null
      : mustChooseShot
        ? `${unknownShotRequested ? "Diese Aufnahme gibt es in der Kamera nicht." : "Alle Pflichtfotos sind aufgenommen."} ${CHOOSE_SHOT_HINT}`
        : unknownShotRequested
          ? UNKNOWN_SHOT_HINT
          : null;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-black text-white">
      {/*
        Top: step + title + progress + the check status line. Short viewports
        (landscape) put the status next to the progress. The check status never
        sits on the framing guide: its full text opens below the header on tap.
      */}
      <header className="pt-safe relative z-30 shrink-0 px-3 pb-2 short:grid short:grid-cols-[minmax(0,1fr)_auto] short:items-center short:pb-1">
        <div className="flex h-14 items-center gap-2 short:col-span-2 short:h-10">
          <Link
            href={exitHref}
            replace
            onNavigate={(event) => {
              if (!isPreviousPage(exitHref)) return;
              event.preventDefault();
              router.back();
            }}
            className="flex size-11 items-center justify-center rounded-xl hover:bg-white/10"
            aria-label="Kamera schließen"
          >
            <ArrowLeft className="size-6" aria-hidden />
          </Link>
          {/* short: the inline position line gets the line height of its text (no taller line box). */}
          <div className="min-w-0 flex-1 text-center short:text-sm short:leading-5">
            <p className="text-sm font-semibold text-white/75 tabular-nums short:inline" aria-label={`Foto ${currentIndex + 1} von ${shots.length}`}>
              {currentIndex + 1} / {shots.length}
            </p>
            {/* The status line below has no room for the counts in short viewports. */}
            <span className="hidden text-sm text-white/60 tabular-nums short:inline"> · noch {remainingCount}</span>
            <h1 className="truncate text-lg leading-tight font-semibold short:text-base">{shot.title}</h1>
          </div>
          <Link
            href={reviewHref}
            className="flex size-11 items-center justify-center rounded-xl hover:bg-white/10"
            aria-label="Fotos überprüfen"
          >
            <LayoutGrid className="size-5" aria-hidden />
          </Link>
        </div>
        <ShotProgress slots={slots} currentKey={shot.key} onSelect={selectShot} checks={checkStates} />
        <div className="mt-1.5 flex min-h-5 flex-wrap items-center justify-center gap-x-3 gap-y-1 text-xs text-white/60 short:mt-0 short:min-h-0 short:flex-nowrap">
          <span className="short:hidden">
            {capturedCount} von {shots.length} aufgenommen · noch {remainingCount}
          </span>
          {failedCount > 0 ? (
            <button
              type="button"
              onClick={() => getAppServices().uploads.retryAll()}
              aria-label={`${failedCount} nicht gespeichert · Erneut versuchen`}
              className="flex items-center gap-1 rounded-full bg-ae-warning/20 px-2 py-0.5 font-semibold text-ae-warning short:ml-3"
            >
              <CloudOff className="size-3" aria-hidden />
              <span>
                {failedCount}
                {/* Short viewports: icon and count only, so the progress keeps its width. */}
                <span className="short:hidden"> nicht gespeichert · Erneut versuchen</span>
              </span>
            </button>
          ) : refusedCount > 0 ? (
            <Link
              href={reviewHref}
              aria-label={`${refusedCount} nicht speicherbar · in „Fotos überprüfen“ verwerfen`}
              className="flex items-center gap-1 rounded-full bg-ae-warning/20 px-2 py-0.5 font-semibold text-ae-warning short:ml-3"
            >
              <CloudOff className="size-3" aria-hidden />
              <span>
                {refusedCount}
                {/* Not longer than the retry action – the full hint is the label. */}
                <span className="short:hidden"> nicht speicherbar · bitte verwerfen</span>
              </span>
            </Link>
          ) : savingCount > 0 ? (
            <span className="flex items-center gap-1 text-white/70 short:ml-3" role="status">
              <Spinner className="size-3" />
              <span className="short:sr-only">Foto wird gespeichert…</span>
            </span>
          ) : null}
          {retakeKeys.length > 0 && (
            <button
              type="button"
              onClick={() => selectShot(retakeKeys[0] as string)}
              aria-label={`${retakeKeys.length === 1 ? "1 Foto" : `${retakeKeys.length} Fotos`} neu aufnehmen · zum ersten springen`}
              className="flex items-center gap-1 rounded-full bg-ae-danger px-2 py-0.5 font-semibold text-white short:ml-3"
            >
              <RotateCcw className="size-3" aria-hidden />
              <span>
                {retakeKeys.length}
                <span className="short:hidden"> neu aufnehmen</span>
              </span>
            </button>
          )}
          {checkOffMessage && (
            <button
              type="button"
              onClick={() => setShowCheckInfo((shown) => !shown)}
              aria-label="Automatische Prüfung aus · Details"
              aria-expanded={showCheckInfo}
              className="flex items-center gap-1 rounded-full bg-white/10 px-2 py-0.5 text-white/70 short:ml-3"
            >
              <ShieldOff className="size-3" aria-hidden />
              <span className="short:hidden">Prüfung aus</span>
            </button>
          )}
          {/* Portrait: a line of its own (its height kept, so the viewfinder never jumps); short: inline. */}
          <CheckStatusLine
            content={statusContent}
            expanded={statusExpanded}
            onToggle={() => setExpandedId(statusExpanded ? null : (statusContent?.id ?? null))}
            className="order-last h-6 basis-full short:order-none short:ml-3 short:max-w-[40vw] short:basis-auto short:empty:hidden"
          />
        </div>
        {statusContent && statusExpanded && (
          <CheckStatusDetails content={statusContent} onSelect={selectShot} onClose={() => setExpandedId(null)} />
        )}
      </header>

      <div className="flex min-h-0 flex-1 flex-col landscape:flex-row">
        {/* Stage: preview sized to the stream's aspect ratio (what you see is what is saved). */}
        <div className="min-h-0 flex-1 px-2">
          <div className="relative size-full" style={{ containerType: "size" }}>
            <div
              className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-xl bg-neutral-900"
              style={{ width: `min(100cqw, calc(100cqh * ${ratio}))`, aspectRatio: String(ratio) }}
            >
              <video
                ref={videoRef}
                className="absolute inset-0 size-full object-cover"
                playsInline
                muted
                autoPlay
                aria-label="Live-Kamerabild"
              />
              <CameraOverlay shot={shot} />

              {/* Only until a shot is chosen (nothing to frame yet while every photo is there). */}
              {!preview && shotHint && (
                <div className="pointer-events-none absolute inset-x-3 top-3 flex flex-col items-center gap-2">
                  <p
                    className="max-w-sm rounded-xl bg-ae-warning px-3 py-1.5 text-center text-xs font-semibold text-black"
                    role="status"
                  >
                    {shotHint}
                  </p>
                </div>
              )}

              <InstructionPanel
                instruction={shot.instruction}
                showRotateHint={shot.category === "exterior" && isPortraitStream}
              />

              {camera.state.status === "starting" && (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-sm text-white/80" role="status">
                  <Spinner className="size-6" />
                  Kamera wird gestartet…
                </div>
              )}
              {camera.state.status === "error" && (
                <CameraErrorPanel code={camera.state.code} onRetry={camera.restart} onUpload={openFilePicker} />
              )}
              {flashKey > 0 && (
                <div key={flashKey} className="pointer-events-none absolute inset-0 animate-flash bg-white opacity-0" />
              )}
              {preview && (
                <CapturePreview
                  url={preview.url}
                  title={preview.shot.title}
                  warnings={preview.warnings}
                  onAccept={() => completePreview(preview)}
                  onRetake={() => setPreview(null)}
                />
              )}
            </div>
          </div>
        </div>

        <div className="shrink-0 landscape:w-44">
          <CaptureControls
            previousThumbnailUrl={previousSlot ? getSlotImageUrl(previousSlot, "original") : null}
            previousTitle={previousSlot?.title ?? null}
            canGoBack={Boolean(previousKey)}
            onBack={() => goTo(previousKey)}
            onShutter={handleShutter}
            shutterLabel={alreadyCaptured ? "Foto wiederholen" : "Foto aufnehmen"}
            shutterDisabled={!streamReady || Boolean(preview) || mustChooseShot}
            busy={busy}
            nextLabel={nextKeyInOrder ? "Weiter" : "Übersicht"}
            onNext={() => (nextKeyInOrder ? goTo(nextKeyInOrder) : router.push(reviewHref))}
            onUpload={openFilePicker}
            uploadDisabled={mustChooseShot}
          />
        </div>
      </div>

      {showCheckInfo && checkOffMessage && (
        <AutoCheckInfo
          message={checkOffMessage}
          askAccessCode={checkSnapshot.availability === "access_code"}
          onAccessCode={handleAccessCode}
          onClose={() => setShowCheckInfo(false)}
        />
      )}

      <input
        ref={fileInputRef}
        type="file"
        accept={PHOTO_FILE_ACCEPT}
        className="hidden"
        onChange={handleFileSelected}
        aria-hidden
        tabIndex={-1}
      />
    </div>
  );
}
