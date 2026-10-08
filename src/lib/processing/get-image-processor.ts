/**
 * Server-side factory: the connected ImageProcessor, or null when no real
 * processor is configured (IMAGE_PROCESSOR is not "real" or the URL is
 * missing). There is no simulated fallback – callers answer
 * PROCESSING_NOT_CONNECTED_MESSAGE then.
 * Only import this from route handlers (server code).
 */
import { getImageProcessorConfig } from "./processor-config";
import { RealImageProcessor } from "./real-image-processor";
import type { ImageProcessor } from "./types";

let cached: { key: string; processor: ImageProcessor } | null = null;

export function getImageProcessor(): ImageProcessor | null {
  const config = getImageProcessorConfig();
  if (config.kind !== "real") return null;
  const key = `${config.baseUrl}\n${config.apiKey ?? ""}`;
  if (cached?.key !== key) {
    cached = { key, processor: new RealImageProcessor({ baseUrl: config.baseUrl, apiKey: config.apiKey }) };
  }
  return cached.processor;
}
