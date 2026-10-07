/**
 * Mock result renderer (browser, demo/mock processing only).
 *
 * Produces a clearly marked PREVIEW file so the "Original / Bearbeitet"
 * workflow can be tested end-to-end. It does NOT segment or composite:
 * the original photo is drawn 1:1 (pixels untouched) and an AutoExperten
 * branding bar is appended BELOW the image. The real pipeline replaces this.
 */
import { BRAND } from "@/config/brand";
import { canvasToBlob, fitWithin, loadImageFromUrl } from "@/lib/camera/image-utils";
import type { ProcessingPresetId } from "@/lib/domain/types";
import { PROCESSING_PRESETS } from "./presets";

/** Keeps canvas sizes within mobile Safari limits. */
const MAX_LONG_EDGE = 4096;

const BAR_THEMES: Record<ProcessingPresetId, { background: string; auto: string; text: string; muted: string }> = {
  autoexperten_standard: { background: "#F2F3F5", auto: "#0B0C0E", text: "#1D2026", muted: "#5B6370" },
  autoexperten_dark: { background: "#0E1013", auto: "#FFFFFF", text: "#E9EBEE", muted: "#9AA1AD" },
  original_plus: { background: "#16181C", auto: "#FFFFFF", text: "#E9EBEE", muted: "#9AA1AD" },
};

export async function renderMockProcessedPreview(
  originalUrl: string,
  preset: ProcessingPresetId,
): Promise<Blob> {
  const image = await loadImageFromUrl(originalUrl);
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
  const theme = BAR_THEMES[preset];
  ctx.fillStyle = theme.background;
  ctx.fillRect(0, size.height, size.width, barHeight);
  ctx.fillStyle = BRAND.colors.blue;
  ctx.fillRect(0, size.height, size.width, Math.max(3, Math.round(barHeight * 0.06)));

  const padding = Math.round(barHeight * 0.4);
  const logoSize = Math.round(barHeight * 0.34);
  const logoBaseline = size.height + barHeight * 0.62;
  const fontStack = "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif";

  // Left: "Auto" + "Experten" wordmark (placeholder until the logo asset exists).
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";
  ctx.font = `700 ${logoSize}px ${fontStack}`;
  ctx.fillStyle = theme.auto;
  ctx.fillText("Auto", padding, logoBaseline);
  const autoWidth = ctx.measureText("Auto").width;
  ctx.fillStyle = BRAND.colors.blue;
  ctx.fillText("Experten", padding + autoWidth, logoBaseline);
  const logoWidth = autoWidth + ctx.measureText("Experten").width;
  ctx.font = `500 ${Math.round(logoSize * 0.55)}px ${fontStack}`;
  ctx.fillStyle = theme.muted;
  ctx.fillText(BRAND.city, padding + logoWidth + padding * 0.4, logoBaseline);

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
