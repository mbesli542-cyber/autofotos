/**
 * GET /api/processing-status – pure decision whether real processing can run.
 *
 * Only a processor reporting the complete set of showroom plates
 * (`showroomSource: "plates"`) is usable. Anything else – "missing", the
 * retired single master photo ("master"), the emergency fallback or no
 * answer – blocks processing with "AutoExperten Showroom-Master fehlt.".
 * In Supabase mode the processor reads and stores the photos itself, so one
 * without storage access (`supabase: false` in its /health) is not connected.
 */
import type { BackendMode } from "@/lib/data/types";
import type { ImageProcessorKind } from "./processor-config";
import {
  PROCESSOR_STORAGE_MISSING_MESSAGE,
  SHOWROOM_CONFIG_ERROR_MESSAGE,
  SHOWROOM_MASTER_MISSING_MESSAGE,
  USABLE_SHOWROOM_SOURCE,
  type ProcessingStatus,
  type ProcessorHealthReport,
} from "./types";

export function buildProcessingStatus(input: {
  processor: ImageProcessorKind;
  /** null: not configured, unreachable or no usable answer. */
  health: ProcessorHealthReport | null;
  accessCodeRequired: boolean;
  dataBackend: BackendMode;
}): ProcessingStatus {
  const { processor, health, accessCodeRequired, dataBackend } = input;
  const base = { processor, accessCodeRequired, dataBackend };
  const notConnected = { ...base, connected: false, showroomSource: null, showroomError: null };
  if (processor !== "real" || !health || !health.authorized || !health.ok) {
    return { ...notConnected, connectionError: null };
  }
  if (dataBackend === "supabase" && !health.storageConnected) {
    return { ...notConnected, connectionError: PROCESSOR_STORAGE_MISSING_MESSAGE };
  }
  let showroomError: string | null = null;
  if (health.presetError) showroomError = SHOWROOM_CONFIG_ERROR_MESSAGE;
  else if (health.showroomSource !== USABLE_SHOWROOM_SOURCE || health.showroomMasterError) {
    showroomError = SHOWROOM_MASTER_MISSING_MESSAGE;
  }
  return { ...base, connected: true, showroomSource: health.showroomSource, showroomError, connectionError: null };
}
