/**
 * Image processor configuration (SERVER ONLY – reads non-public env vars).
 *
 *   IMAGE_PROCESSOR=real               + IMAGE_PROCESSING_API_URL (+ IMAGE_PROCESSING_API_KEY)
 *                                        → the Python processor (processor/) is connected
 *   IMAGE_PROCESSOR=mock (default)     → NO processor connected. There are no simulated
 *                                        results: processing is refused with
 *                                        "Echte Showroom-Bearbeitung ist noch nicht verbunden."
 *
 * Independent of the data backend (NEXT_PUBLIC_DATA_BACKEND): demo storage
 * and real processing work together (demo mode uploads the photo).
 */

export type ImageProcessorKind = "mock" | "real";

export interface ProcessorConnection {
  /** IMAGE_PROCESSING_API_URL, may contain a path prefix. */
  baseUrl: string;
  /** IMAGE_PROCESSING_API_KEY – sent as Bearer token if set. */
  apiKey: string | null;
}

export type ImageProcessorConfig =
  | ({ kind: "real" } & ProcessorConnection)
  | { kind: "mock"; reason: "disabled" | "missing_url" | "invalid_url" };

interface ProcessorEnv {
  IMAGE_PROCESSOR?: string;
  IMAGE_PROCESSING_API_URL?: string;
  IMAGE_PROCESSING_API_KEY?: string;
}

/** Validated processor URL + key, or null when the URL is missing/invalid. */
export function parseProcessorConnection(
  env: Pick<ProcessorEnv, "IMAGE_PROCESSING_API_URL" | "IMAGE_PROCESSING_API_KEY">,
): ProcessorConnection | { error: "missing_url" | "invalid_url" } {
  const baseUrl = env.IMAGE_PROCESSING_API_URL?.trim();
  if (!baseUrl) return { error: "missing_url" };
  try {
    const { protocol } = new URL(baseUrl);
    if (protocol !== "http:" && protocol !== "https:") return { error: "invalid_url" };
  } catch {
    return { error: "invalid_url" };
  }
  const apiKey = env.IMAGE_PROCESSING_API_KEY?.trim();
  return { baseUrl, apiKey: apiKey ? apiKey : null };
}

/** Pure decision from the environment. */
export function resolveImageProcessorConfig(env: ProcessorEnv): ImageProcessorConfig {
  if (env.IMAGE_PROCESSOR?.trim().toLowerCase() !== "real") {
    return { kind: "mock", reason: "disabled" };
  }
  const connection = parseProcessorConnection(env);
  if ("error" in connection) return { kind: "mock", reason: connection.error };
  return { kind: "real", ...connection };
}

/** Reads the configuration at request time (never frozen into a build). */
export function getImageProcessorConfig(): ImageProcessorConfig {
  return resolveImageProcessorConfig({
    IMAGE_PROCESSOR: process.env.IMAGE_PROCESSOR,
    IMAGE_PROCESSING_API_URL: process.env.IMAGE_PROCESSING_API_URL,
    IMAGE_PROCESSING_API_KEY: process.env.IMAGE_PROCESSING_API_KEY,
  });
}
