import { describe, expect, it } from "vitest";
import { PROCESSING_PRESET_IDS } from "@/lib/domain/types";
import {
  DEFAULT_PROCESSING_PRESET,
  PROCESSING_PRESETS,
  PROCESSING_PRESET_LIST,
  isProcessingPresetAvailable,
} from "./presets";

describe("processing presets", () => {
  it("offers only the style the processor implements", () => {
    // processor/app/presets.py PRESET_FILES: autoexperten_standard only.
    expect(PROCESSING_PRESET_IDS.filter(isProcessingPresetAvailable)).toEqual(["autoexperten_standard"]);
    expect(isProcessingPresetAvailable("autoexperten_dark")).toBe(false);
    expect(isProcessingPresetAvailable("original_plus")).toBe(false);
  });

  it("starts with an available default and still lists the coming styles", () => {
    expect(isProcessingPresetAvailable(DEFAULT_PROCESSING_PRESET)).toBe(true);
    expect(PROCESSING_PRESET_LIST.map((preset) => preset.id)).toEqual(Object.keys(PROCESSING_PRESETS));
  });
});
