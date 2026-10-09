/**
 * GET /api/processing-status – pure decision whether real processing can run.
 *
 * Only a processor reporting the complete set of showroom plates
 * (`showroomSource: "plates"`) is usable. Anything else – "missing", the
 * retired single master photo ("master"), the emergency fallback or no
 * answer – blocks processing with "AutoExperten Showroom-Master fehlt.".
 */
import type { BackendMode } from "@/lib/data/types";
import type { ImageProcessorKind } from "./processor-config";
import {
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
  if (processor !== "real" || !health || !health.authorized || !health.ok) {
    return { ...base, connected: false, showroomSource: null, showroomError: null };
  }
  let showroomError: string | null = null;
  if (health.presetError) showroomError = SHOWROOM_CONFIG_ERROR_MESSAGE;
  else if (health.showroomSource !== USABLE_SHOWROOM_SOURCE || health.showroomMasterError) {
    showroomError = SHOWROOM_MASTER_MISSING_MESSAGE;
  }
  return { ...base, connected: true, showroomSource: health.showroomSource, showroomError };
}
