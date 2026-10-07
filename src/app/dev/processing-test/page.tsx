import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { ProcessingTestScreen } from "@/features/dev/ProcessingTestScreen";
import { isDevToolsEnabled } from "@/lib/dev-tools";

export const metadata: Metadata = { title: "Bildverarbeitung testen" };

/**
 * ENABLE_DEV_TOOLS is a runtime switch, so this page renders per request
 * (blocking, no static shell) and can answer with a real 404 status.
 */
export const instant = false;

/** Developer test page for the image processor – not linked from the app. */
export default async function ProcessingTestPage() {
  await connection();
  if (!isDevToolsEnabled()) notFound();
  return <ProcessingTestScreen />;
}
