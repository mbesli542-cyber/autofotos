/**
 * UI hint for the FIRST render of the processing page only
 * (NEXT_PUBLIC_IMAGE_PROCESSOR=real → "Verbindung wird geprüft…" instead of
 * "nicht verbunden"). The real state always comes from GET /api/processing-status.
 */
export const PROCESSOR_UI_HINT: "real" | "mock" =
  process.env.NEXT_PUBLIC_IMAGE_PROCESSOR === "real" ? "real" : "mock";
