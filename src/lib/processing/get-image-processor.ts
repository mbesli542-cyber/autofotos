/**
 * Server-side factory: selects the ImageProcessor implementation.
 * Only import this from route handlers (server code).
 */
import { MockImageProcessor } from "./mock-image-processor";
import { RealImageProcessor } from "./real-image-processor";
import type { ImageProcessor } from "./types";

let cached: ImageProcessor | null = null;

export function getImageProcessor(): ImageProcessor {
  if (cached) return cached;

  const kind = process.env.IMAGE_PROCESSOR ?? "mock";
  const baseUrl = process.env.IMAGE_PROCESSING_API_URL;

  if (kind === "real") {
    if (!baseUrl) {
      throw new Error(
        "IMAGE_PROCESSOR=real requires IMAGE_PROCESSING_API_URL to be set.",
      );
    }
    cached = new RealImageProcessor({
      baseUrl,
      apiKey: process.env.IMAGE_PROCESSING_API_KEY ?? null,
    });
  } else {
    cached = new MockImageProcessor();
  }
  return cached;
}
