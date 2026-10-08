/**
 * GET /api/processing-status – pure decision whether real processing can run.
 */
import type { BackendMode } from "@/lib/data/types";
import type { ImageProcessorKind } from "./processor-config";
import {
  SHOWROOM_CONFIG_ERROR_MESSAGE,
  SHOWROOM_MASTER_MISSING_MESSAGE,
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
  else if (health.showroomSource !== "master" || health.showroomMasterError) {
    showroomError = SHOWROOM_MASTER_MISSING_MESSAGE;
  }
  return { ...base, connected: true, showroomSource: health.showroomSource, showroomError };
}
