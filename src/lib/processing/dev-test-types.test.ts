import { describe, expect, it } from "vitest";
import {
  DEV_SHOWROOM_WIDTH,
  DEV_TEST_API,
  parseProcessorHealth,
  parseProcessorJob,
  parseShowroomSource,
  parseShowroomWidth,
  showroomPreviewWidth,
} from "./dev-test-types";

function job(metadata: unknown) {
  return parseProcessorJob({
    jobId: "job_1",
    status: "complete",
    preset: "autoexperten_standard",
    metadata,
  });
}

describe("parseShowroomSource", () => {
  it("accepts only the known values", () => {
    expect(parseShowroomSource("master")).toBe("master");
    expect(parseShowroomSource("fallback")).toBe("fallback");
    for (const value of ["Master", "placeholder", "", null, undefined, 1, true, {}]) {
      expect(parseShowroomSource(value)).toBeNull();
    }
  });
});

describe("showroom status in job metadata", () => {
  it("reads showroomSource and keeps showroomPlaceholder consistent", () => {
    expect(job({ showroomSource: "master", showroomPlaceholder: false })?.metadata).toMatchObject({
      showroomSource: "master",
      showroomPlaceholder: false,
    });
    expect(job({ showroomSource: "fallback" })?.metadata).toMatchObject({
      showroomSource: "fallback",
      showroomPlaceholder: true,
    });
  });

  it("treats the legacy placeholder flag as fallback (warn rather than claim the master)", () => {
    expect(job({ showroomPlaceholder: true })?.metadata).toMatchObject({
      showroomSource: "fallback",
      showroomPlaceholder: true,
    });
    expect(job({ showroomSource: "master", showroomPlaceholder: true })?.metadata).toMatchObject({
      showroomSource: "fallback",
      showroomPlaceholder: true,
    });
  });

  it("defaults to unknown for missing or invalid values", () => {
    for (const metadata of [undefined, {}, { showroomSource: "studio" }, { showroomPlaceholder: "true" }]) {
      expect(job(metadata)?.metadata).toMatchObject({ showroomSource: null, showroomPlaceholder: false });
    }
  });

  it("keeps filtering debug file names", () => {
    expect(
      job({ debugFiles: ["mask.png", "../secret", ".env", "background.jpg", 3] })?.metadata.debugFiles,
    ).toEqual(["mask.png", "background.jpg"]);
  });
});

describe("parseProcessorHealth", () => {
  it("reads showroomSource", () => {
    expect(parseProcessorHealth({ status: "ok", showroomSource: "master" })).toMatchObject({
      showroomSource: "master",
      showroomPlaceholder: false,
    });
    expect(parseProcessorHealth({ status: "ok", showroomSource: "fallback" })).toMatchObject({
      showroomSource: "fallback",
      showroomPlaceholder: true,
    });
    expect(parseProcessorHealth({ status: "ok", showroomPlaceholder: true })).toMatchObject({
      showroomSource: "fallback",
    });
    expect(parseProcessorHealth({ status: "ok" })).toMatchObject({
      showroomSource: null,
      showroomPlaceholder: false,
    });
  });

  it("still rejects unusable responses", () => {
    expect(parseProcessorHealth({ status: "error", showroomSource: "master" })).toBeNull();
    expect(parseProcessorHealth(null)).toBeNull();
  });
});

describe("parseShowroomWidth", () => {
  it("uses the default when the parameter is absent", () => {
    expect(parseShowroomWidth(null)).toBe(DEV_SHOWROOM_WIDTH.default);
  });

  it("accepts integers within the limits", () => {
    expect(parseShowroomWidth("320")).toBe(320);
    expect(parseShowroomWidth("1600")).toBe(1600);
    expect(parseShowroomWidth("3840")).toBe(3840);
  });

  it("rejects everything else", () => {
    for (const value of ["", "319", "3841", "0", "-800", "1600.5", "1e3", " 1600", "+1600", "abc", "999999"]) {
      expect(parseShowroomWidth(value)).toBeNull();
    }
  });
});

describe("showroomPreviewWidth", () => {
  it("matches the preferred width within the limits", () => {
    expect(showroomPreviewWidth(2048)).toBe(2048);
    expect(showroomPreviewWidth(100)).toBe(DEV_SHOWROOM_WIDTH.min);
    expect(showroomPreviewWidth(8000)).toBe(DEV_SHOWROOM_WIDTH.max);
    expect(showroomPreviewWidth(null)).toBe(DEV_SHOWROOM_WIDTH.default);
    expect(showroomPreviewWidth(0)).toBe(DEV_SHOWROOM_WIDTH.default);
    expect(showroomPreviewWidth(0.5)).toBe(DEV_SHOWROOM_WIDTH.default);
    expect(showroomPreviewWidth(Number.NaN)).toBe(DEV_SHOWROOM_WIDTH.default);
  });
});

describe("DEV_TEST_API.showroom", () => {
  it("builds the proxy URL", () => {
    expect(DEV_TEST_API.showroom("autoexperten_standard")).toBe(
      "/api/dev/processing-test/showroom?preset=autoexperten_standard&width=1600",
    );
    expect(DEV_TEST_API.showroom("autoexperten_standard", 2048)).toBe(
      "/api/dev/processing-test/showroom?preset=autoexperten_standard&width=2048",
    );
  });
});
