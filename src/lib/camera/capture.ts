/**
 * Capturing a full-resolution still from the live camera.
 *
 * 1. ImageCapture.takePhoto() where available (Chrome/Android): uses the full
 *    sensor resolution. Its orientation is verified against the preview; on
 *    any mismatch we fall back to (2) so photos are never rotated wrongly.
 * 2. Canvas grab of the current video frame at the stream's native
 *    resolution (not the on-screen size).
 */
import { CAMERA_CONFIG } from "./config";
import { canvasToBlob, decodeImage } from "./image-utils";

interface ImageCaptureLike {
  takePhoto(): Promise<Blob>;
}

type ImageCaptureConstructor = new (track: MediaStreamTrack) => ImageCaptureLike;

function getImageCaptureConstructor(): ImageCaptureConstructor | null {
  const candidate = (globalThis as { ImageCapture?: ImageCaptureConstructor }).ImageCapture;
  return typeof candidate === "function" ? candidate : null;
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("timeout")), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

async function takePhotoWithImageCapture(
  track: MediaStreamTrack,
  video: HTMLVideoElement,
): Promise<Blob | null> {
  const ImageCaptureCtor = getImageCaptureConstructor();
  if (!ImageCaptureCtor || track.readyState !== "live") return null;
  try {
    const blob = await withTimeout(
      new ImageCaptureCtor(track).takePhoto(),
      CAMERA_CONFIG.takePhotoTimeoutMs,
    );
    if (!blob || blob.size === 0) return null;
    // Orientation safety check: photo must match the preview orientation.
    const decoded = await decodeImage(blob);
    const photoLandscape = decoded.width >= decoded.height;
    decoded.release();
    const previewLandscape = video.videoWidth >= video.videoHeight;
    return photoLandscape === previewLandscape ? blob : null;
  } catch {
    return null;
  }
}

async function grabVideoFrame(video: HTMLVideoElement): Promise<Blob> {
  const width = video.videoWidth;
  const height = video.videoHeight;
  if (!width || !height) throw new Error("Kamera ist noch nicht bereit.");
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Canvas nicht verfügbar.");
  context.drawImage(video, 0, 0, width, height);
  return canvasToBlob(canvas, "image/jpeg", CAMERA_CONFIG.jpegQuality);
}

export async function captureStill(
  video: HTMLVideoElement,
  track: MediaStreamTrack | null,
): Promise<Blob> {
  if (track) {
    const photo = await takePhotoWithImageCapture(track, video);
    if (photo) return photo;
  }
  return grabVideoFrame(video);
}
