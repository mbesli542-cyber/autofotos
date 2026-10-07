/** Tunables for the guided camera. */
export const CAMERA_CONFIG = {
  /** Opacity of the vehicle framing guide (0..1). Recommended 0.30–0.45. */
  overlayOpacity: 0.4,
  /** Show the rule-of-thirds grid behind the guide. */
  showGrid: true,
  /** JPEG quality for frames grabbed from the video stream. */
  jpegQuality: 0.92,
  /** How long the captured photo is shown before auto-advancing (ms). */
  capturePreviewMs: 900,
  /** Longest edge of generated thumbnails (px). */
  thumbnailMaxEdge: 480,
  /** Requested stream resolution – browsers pick the closest supported. */
  idealWidth: 3840,
  idealHeight: 2160,
  /** Max time to wait for ImageCapture.takePhoto() before falling back (ms). */
  takePhotoTimeoutMs: 3500,
} as const;
