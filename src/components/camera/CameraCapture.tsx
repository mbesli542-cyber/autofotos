"use client";

import { ArrowLeft, CircleCheck, CloudOff, LayoutGrid } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import { getSlotImageUrl } from "@/components/photos/ShotThumbnail";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { useCameraStream } from "@/hooks/use-camera-stream";
import { usePendingUploads } from "@/hooks/use-upload-queue";
import { getAppServices } from "@/lib/app-services";
import { cameraReturnHref, type CameraReturnTarget } from "@/lib/camera/camera-links";
import { captureStill } from "@/lib/camera/capture";
import { CAMERA_CONFIG } from "@/lib/camera/config";
import { noopQualityChecker, type CaptureQualityWarning } from "@/lib/camera/quality";
import type { Vehicle, VehiclePhotoWithUrls } from "@/lib/domain/types";
import { buildPhotoSlots, isSlotCaptured } from "@/lib/photos/photo-slots";
import {
  getAdjacentShotKey,
  getFirstMissingShotKey,
  getNextShotKeyAfterCapture,
} from "@/lib/shots/shot-progress";
import { getOrderedShots, getShot, type ShotDefinition, type ShotTemplate } from "@/lib/shots/shot-template";
import { CameraErrorPanel } from "./CameraErrorPanel";
import { CameraOverlay } from "./CameraOverlay";
import { CapturePreview } from "./CapturePreview";
import { CaptureControls } from "./CaptureControls";
import { InstructionPanel } from "./InstructionPanel";
import { ShotProgress } from "./ShotProgress";

interface PreviewState {
  url: string;
  shot: ShotDefinition;
  warnings: CaptureQualityWarning[];
  nextKey: string | null;
}

/**
 * Guided capture: shows the current shot, its framing guide and progress,
 * captures a full-resolution photo, stores it (offline-safe queue) and
 * automatically advances to the next missing shot – or, with `returnTo`
 * (retake of one shot from "Fotos überprüfen" / "Fotos bearbeiten"), goes
 * back there.
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
  const camera = useCameraStream(videoRef);
  const pending = usePendingUploads(vehicle.id);

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [flashKey, setFlashKey] = useState(0);
  const [preview, setPreview] = useState<PreviewState | null>(null);

  const reviewHref = cameraReturnHref(vehicle.id, "fotos");
  /** Close button / after a single retake: back to where the retake was started. */
  const exitHref = cameraReturnHref(vehicle.id, returnTo);
  const shots = useMemo(() => getOrderedShots(template).filter((s) => s.required), [template]);
  const slots = useMemo(() => buildPhotoSlots(template, photos, pending).required, [template, photos, pending]);
  const capturedKeys = useMemo(
    () => new Set(slots.filter(isSlotCaptured).map((slot) => slot.key)),
    [slots],
  );

  const fallbackKey =
    (initialShotKey && getShot(template, initialShotKey) ? initialShotKey : null) ??
    getFirstMissingShotKey(template, capturedKeys) ??
    shots[0]?.key ??
    "";
  const currentKey = selectedKey ?? fallbackKey;
  const shot = getShot(template, currentKey) ?? shots[0];
  const currentIndex = shots.findIndex((s) => s.key === shot?.key);
  const currentSlot = slots[currentIndex];
  const previousKey = shot ? getAdjacentShotKey(template, shot.key, -1) : null;
  const previousSlot = slots.find((slot) => slot.key === previousKey) ?? null;
  const nextKeyInOrder = shot ? getAdjacentShotKey(template, shot.key, 1) : null;

  const failedCount = pending.filter((item) => item.status === "failed").length;
  const savingCount = pending.length - failedCount;
  const capturedCount = capturedKeys.size;

  const completePreview = useCallback(
    (state: PreviewState) => {
      URL.revokeObjectURL(state.url);
      setPreview(null);
      if (returnTo) {
        router.push(exitHref);
      } else if (state.nextKey) {
        setSelectedKey(state.nextKey);
      } else {
        toast.success("Alle Pflichtfotos aufgenommen. Bitte prüfen Sie die Fotos.");
        router.push(reviewHref);
      }
    },
    [returnTo, exitHref, reviewHref, router, toast],
  );

  // Show the captured photo briefly, then advance automatically.
  useEffect(() => {
    if (!preview || preview.warnings.length > 0) return;
    const timer = setTimeout(() => completePreview(preview), CAMERA_CONFIG.capturePreviewMs);
    return () => clearTimeout(timer);
  }, [preview, completePreview]);

  async function storeCapture(file: Blob, target: ShotDefinition) {
    const { uploads } = getAppServices();
    try {
      // Persisted locally first – never lost if the upload fails.
      await uploads.enqueue({
        vehicleId: vehicle.id,
        shotKey: target.key,
        shotOrder: target.order,
        title: target.title,
        file,
        takenAt: new Date().toISOString(),
      });
    } catch {
      toast.error("Foto konnte nicht gespeichert werden. Bitte versuchen Sie es erneut.");
      return;
    }
    // Keep this shot selected while its preview is shown (the background
    // upload may otherwise change the auto-selected "first missing" shot).
    setSelectedKey(target.key);
    const quality = await noopQualityChecker.analyzeCapture(file, target);
    const nextKey = getNextShotKeyAfterCapture(template, new Set([...capturedKeys, target.key]), target.key);
    setPreview({ url: URL.createObjectURL(file), shot: target, warnings: quality.warnings, nextKey });
  }

  async function handleShutter() {
    const video = videoRef.current;
    if (!video || !shot || busy || preview || camera.state.status !== "ready") return;
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
    if (!file || !shot) return;
    if (!file.type.startsWith("image/")) {
      toast.error("Bitte wählen Sie eine Bilddatei aus.");
      return;
    }
    setBusy(true);
    try {
      // Uploaded files are stored unchanged (original quality and EXIF).
      await storeCapture(file, shot);
    } finally {
      setBusy(false);
    }
  }

  function goTo(key: string | null) {
    if (key) setSelectedKey(key);
  }

  if (!shot || !currentSlot) return null;

  const streamReady = camera.state.status === "ready";
  const ratio = streamReady && camera.state.status === "ready" ? camera.state.width / camera.state.height : 4 / 3;
  const isPortraitStream = streamReady && ratio < 1;
  const alreadyCaptured = isSlotCaptured(currentSlot);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-black text-white">
      {/* Top: step + title + progress */}
      <header className="pt-safe shrink-0 px-3 pb-2 short:pb-1">
        <div className="flex h-14 items-center gap-2 short:h-10">
          <Link
            href={exitHref}
            className="flex size-11 items-center justify-center rounded-xl hover:bg-white/10"
            aria-label="Kamera schließen"
          >
            <ArrowLeft className="size-6" aria-hidden />
          </Link>
          <div className="min-w-0 flex-1 text-center">
            <p className="text-sm font-semibold text-white/75 tabular-nums" aria-label={`Foto ${currentIndex + 1} von ${shots.length}`}>
              {currentIndex + 1} / {shots.length}
            </p>
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
        <ShotProgress slots={slots} currentKey={shot.key} onSelect={goTo} />
        <div className="mt-1.5 flex min-h-5 items-center justify-center gap-3 text-xs text-white/60 short:hidden">
          <span>
            {capturedCount} von {shots.length} aufgenommen · noch {shots.length - capturedCount}
          </span>
          {failedCount > 0 ? (
            <button
              type="button"
              onClick={() => getAppServices().uploads.retryAll()}
              className="flex items-center gap-1 rounded-full bg-ae-warning/20 px-2 py-0.5 font-semibold text-ae-warning"
            >
              <CloudOff className="size-3" aria-hidden />
              {failedCount} nicht gespeichert · Erneut versuchen
            </button>
          ) : savingCount > 0 ? (
            <span className="flex items-center gap-1 text-white/70" role="status">
              <Spinner className="size-3" />
              Foto wird gespeichert…
            </span>
          ) : null}
        </div>
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

              {alreadyCaptured && !preview && (
                <span className="absolute top-3 left-1/2 flex -translate-x-1/2 items-center gap-1 rounded-full bg-black/60 px-2.5 py-1 text-xs font-semibold text-white backdrop-blur">
                  <CircleCheck className="size-3.5 text-ae-blue" aria-hidden />
                  Bereits aufgenommen
                </span>
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
                <CameraErrorPanel
                  code={camera.state.code}
                  onRetry={camera.restart}
                  onUpload={() => fileInputRef.current?.click()}
                />
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
                  onRetake={() => {
                    URL.revokeObjectURL(preview.url);
                    setPreview(null);
                  }}
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
            shutterDisabled={!streamReady || Boolean(preview)}
            busy={busy}
            nextLabel={nextKeyInOrder ? "Weiter" : "Übersicht"}
            onNext={() => (nextKeyInOrder ? goTo(nextKeyInOrder) : router.push(reviewHref))}
            onUpload={() => fileInputRef.current?.click()}
          />
        </div>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={handleFileSelected}
        aria-hidden
        tabIndex={-1}
      />
    </div>
  );
}
