/**
 * Browser image helpers. Originals are NEVER re-encoded – these helpers only
 * read dimensions and create separate thumbnails.
 *
 * Orientation: decoding uses `imageOrientation: "from-image"` (EXIF aware),
 * with an <img> fallback, which modern browsers also orient correctly.
 */

interface DecodedImage {
  source: CanvasImageSource;
  width: number;
  height: number;
  release: () => void;
}

function loadImageElement(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.decoding = "async";
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("Bild konnte nicht geladen werden."));
    image.src = src;
  });
}

export async function decodeImage(blob: Blob): Promise<DecodedImage> {
  if (typeof createImageBitmap === "function") {
    try {
      const bitmap = await createImageBitmap(blob, { imageOrientation: "from-image" });
      return {
        source: bitmap,
        width: bitmap.width,
        height: bitmap.height,
        release: () => bitmap.close(),
      };
    } catch {
      // e.g. SVG or unsupported options – use <img> below
    }
  }
  const url = URL.createObjectURL(blob);
  try {
    const image = await loadImageElement(url);
    return {
      source: image,
      width: image.naturalWidth,
      height: image.naturalHeight,
      release: () => URL.revokeObjectURL(url),
    };
  } catch (error) {
    URL.revokeObjectURL(url);
    throw error;
  }
}

/** Loads an image from a URL (e.g. signed URL) for canvas drawing. */
export async function loadImageFromUrl(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.crossOrigin = "anonymous";
    image.decoding = "async";
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("Bild konnte nicht geladen werden."));
    image.src = url;
  });
}

export function canvasToBlob(
  canvas: HTMLCanvasElement,
  type = "image/jpeg",
  quality = 0.92,
): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("Bild konnte nicht erzeugt werden."))),
      type,
      quality,
    );
  });
}

/** Scales (width, height) so the longest edge is at most `maxEdge`. */
export function fitWithin(width: number, height: number, maxEdge: number) {
  const scale = Math.min(1, maxEdge / Math.max(width, height));
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

export interface PreparedImage {
  width: number | null;
  height: number | null;
  thumbnail: Blob | null;
}

/** Reads oriented dimensions and renders a small JPEG thumbnail. */
export async function prepareImage(file: Blob, thumbnailMaxEdge: number): Promise<PreparedImage> {
  let decoded: DecodedImage;
  try {
    decoded = await decodeImage(file);
  } catch {
    return { width: null, height: null, thumbnail: null };
  }
  try {
    const size = fitWithin(decoded.width, decoded.height, thumbnailMaxEdge);
    const canvas = document.createElement("canvas");
    canvas.width = size.width;
    canvas.height = size.height;
    const context = canvas.getContext("2d");
    if (!context) return { width: decoded.width, height: decoded.height, thumbnail: null };
    context.imageSmoothingQuality = "high";
    context.drawImage(decoded.source, 0, 0, size.width, size.height);
    const thumbnail = await canvasToBlob(canvas, "image/jpeg", 0.8).catch(() => null);
    return { width: decoded.width, height: decoded.height, thumbnail };
  } finally {
    decoded.release();
  }
}
