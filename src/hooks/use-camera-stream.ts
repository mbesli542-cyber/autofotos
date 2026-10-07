"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import {
  mapCameraError,
  openCameraStream,
  stopStream,
  type CameraErrorCode,
} from "@/lib/camera/camera-stream";

export type CameraStreamState =
  | { status: "starting" }
  | { status: "ready"; width: number; height: number }
  | { status: "error"; code: CameraErrorCode };

/**
 * Opens the rear camera into the given <video> element and stops it on
 * unmount / when the route gets hidden. Restarts automatically when the
 * app returns to the foreground after the OS stopped the camera.
 */
export function useCameraStream(videoRef: RefObject<HTMLVideoElement | null>) {
  const [state, setState] = useState<CameraStreamState>({ status: "starting" });
  const [attempt, setAttempt] = useState(0);
  const trackRef = useRef<MediaStreamTrack | null>(null);

  useEffect(() => {
    let cancelled = false;
    let stream: MediaStream | null = null;
    const video = videoRef.current;

    const updateSize = () => {
      if (!video || cancelled || !video.videoWidth || !video.videoHeight) return;
      setState({ status: "ready", width: video.videoWidth, height: video.videoHeight });
    };

    const handleVisibility = () => {
      if (document.visibilityState === "visible" && trackRef.current?.readyState === "ended") {
        setState({ status: "starting" });
        setAttempt((value) => value + 1);
      }
    };

    openCameraStream()
      .then(async (opened) => {
        if (cancelled || !video) {
          stopStream(opened);
          return;
        }
        stream = opened;
        trackRef.current = opened.getVideoTracks()[0] ?? null;
        video.srcObject = opened;
        video.addEventListener("resize", updateSize);
        video.addEventListener("loadedmetadata", updateSize);
        await video.play().catch(() => undefined);
        updateSize();
      })
      .catch((error: unknown) => {
        if (!cancelled) setState({ status: "error", code: mapCameraError(error) });
      });

    document.addEventListener("visibilitychange", handleVisibility);

    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", handleVisibility);
      video?.removeEventListener("resize", updateSize);
      video?.removeEventListener("loadedmetadata", updateSize);
      stopStream(stream);
      trackRef.current = null;
      if (video) video.srcObject = null;
    };
  }, [videoRef, attempt]);

  const restart = useCallback(() => {
    setState({ status: "starting" });
    setAttempt((value) => value + 1);
  }, []);

  return { state, trackRef, restart };
}
