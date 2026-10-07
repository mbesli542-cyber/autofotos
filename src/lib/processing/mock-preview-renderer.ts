/**
 * Mock result renderer (browser, demo/mock processing only).
 *
 * Produces a clearly marked PREVIEW file so the "Original / Bearbeitet"
 * workflow can be tested end-to-end. It does NOT segment or composite:
 * the original photo is drawn 1:1 (pixels untouched) and an AutoExperten
 * branding bar is appended BELOW the image. The real pipeline replaces this.
 */
import { BRAND, LOGO_ASSETS } from "@/config/brand";
import { canvasToBlob, fitWithin, loadImageFromUrl } from "@/lib/camera/image-utils";
import type { ProcessingPresetId } from "@/lib/domain/types";
import { PROCESSING_PRESETS } from "./presets";

/** Keeps canvas sizes within mobile Safari limits. */
const MAX_LONG_EDGE = 4096;

interface BarTheme {
  background: string;
  /** Official logo file matching the bar background (never redrawn as text). */
  logo: string;
  text: string;
  muted: string;
}

const BAR_THEMES: Record<ProcessingPresetId, BarTheme> = {
  autoexperten_standard: { background: "#F2F3F5", logo: LOGO_ASSETS.onLight, text: "#1D2026", muted: "#5B6370" },
  autoexperten_dark: { background: "#0E1013", logo: LOGO_ASSETS.onDark, text: "#E9EBEE", muted: "#9AA1AD" },
  original_plus: { background: "#16181C", logo: LOGO_ASSETS.onDark, text: "#E9EBEE", muted: "#9AA1AD" },
};

export async function renderMockProcessedPreview(
  originalUrl: string,
  preset: ProcessingPresetId,
): Promise<Blob> {
  const theme = BAR_THEMES[preset];
  const [image, logo] = await Promise.all([
    loadImageFromUrl(originalUrl),
    // A missing logo must not break the preview – the bar is then drawn without it.
    loadImageFromUrl(theme.logo).catch(() => null),
  ]);
  const size = fitWithin(image.naturalWidth || 1600, image.naturalHeight || 1200, MAX_LONG_EDGE);
  const barHeight = Math.round(Math.max(72, size.width * 0.085));

  const canvas = document.createElement("canvas");
  canvas.width = size.width;
  canvas.height = size.height + barHeight;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas nicht verfügbar.");

  // 1. Original photo, unchanged.
  ctx.drawImage(image, 0, 0, size.width, size.height);

  // 2. Branding bar below the photo.
  ctx.fillStyle = theme.background;
  ctx.fillRect(0, size.height, size.width, barHeight);
  ctx.fillStyle = BRAND.colors.blue;
  ctx.fillRect(0, size.height, size.width, Math.max(3, Math.round(barHeight * 0.06)));

  const padding = Math.round(barHeight * 0.4);
  const logoSize = Math.round(barHeight * 0.34);
  const logoBaseline = size.height + barHeight * 0.62;
  const fontStack = "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif";

  // Left: official AutoExperten logo file (scaled only), followed by the city.
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";
  let logoWidth = 0;
  if (logo) {
    const logoHeight = Math.round(logoSize * 1.1);
    const aspect = (logo.naturalWidth || LOGO_ASSETS.width) / (logo.naturalHeight || LOGO_ASSETS.height);
    logoWidth = Math.round(logoHeight * aspect);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    // The letters' baseline sits at ~76 % of the logo height ("p" descends below).
    ctx.drawImage(logo, padding, Math.round(logoBaseline - logoHeight * 0.76), logoWidth, logoHeight);
  }
  ctx.font = `500 ${Math.round(logoSize * 0.55)}px ${fontStack}`;
  ctx.fillStyle = theme.muted;
  ctx.fillText(BRAND.city, padding + logoWidth + (logo ? padding * 0.4 : 0), logoBaseline);

  // Right: contact line + unmistakable mock marker (inside the bar, never on the photo).
  ctx.textAlign = "right";
  ctx.font = `500 ${Math.round(logoSize * 0.56)}px ${fontStack}`;
  ctx.fillStyle = theme.text;
  ctx.fillText(`${BRAND.websiteLabel}  ·  ${BRAND.phone}`, size.width - padding, size.height + barHeight * 0.48);
  ctx.font = `600 ${Math.round(logoSize * 0.42)}px ${fontStack}`;
  ctx.fillStyle = BRAND.colors.blue;
  ctx.fillText(
    `VORSCHAU · ${PROCESSING_PRESETS[preset].name} · Bearbeitung simuliert`,
    size.width - padding,
    size.height + barHeight * 0.78,
  );

  return canvasToBlob(canvas, "image/jpeg", 0.9);
}
