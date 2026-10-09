import { describe, expect, it } from "vitest";
import { DEFAULT_SHOT_TEMPLATE, getOrderedShots } from "@/lib/shots/shot-template";
import {
  DEV_SHOWROOM_WIDTH,
  DEV_TEST_API,
  SHOWROOM_PLATE_SHOTS,
  isShowroomPlateShot,
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
    expect(parseShowroomSource("plates")).toBe("plates");
    expect(parseShowroomSource("missing")).toBe("missing");
    expect(parseShowroomSource("master")).toBe("master");
    expect(parseShowroomSource("fallback")).toBe("fallback");
    for (const value of ["Plates", "placeholder", "", null, undefined, 1, true, {}]) {
      expect(parseShowroomSource(value)).toBeNull();
    }
  });
});

describe("SHOWROOM_PLATE_SHOTS", () => {
  it("are exactly the exterior shots of the template, in order", () => {
    const exterior = getOrderedShots(DEFAULT_SHOT_TEMPLATE)
      .filter((shot) => shot.category === "exterior")
      .map((shot) => shot.key);
    expect([...SHOWROOM_PLATE_SHOTS]).toEqual(exterior);
  });

  it("rejects interior, extra and unknown shots", () => {
    expect(isShowroomPlateShot("rear_right_45")).toBe(true);
    for (const value of ["cockpit", "wheel_detail", "extra_01", "../front", "FRONT", "", null, 3]) {
      expect(isShowroomPlateShot(value)).toBe(false);
    }
  });
});

describe("showroom status in job metadata", () => {
  it("reads showroomSource and keeps showroomPlaceholder consistent", () => {
    expect(job({ showroomSource: "plates", showroomPlaceholder: false })?.metadata).toMatchObject({
      showroomSource: "plates",
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
    expect(job({ showroomSource: "plates", showroomPlaceholder: true })?.metadata).toMatchObject({
      showroomSource: "fallback",
      showroomPlaceholder: true,
    });
  });

  it("reads plateUsed, errorCode and keeps the other fields as details", () => {
    const metadata = job({
      showroomSource: "plates",
      plateUsed: "front_right_45",
      errorCode: "perspective_mismatch",
      qualityGate: { contactRiseRatio: 0.41 },
      placement: { targetWidthRatio: 0.81, achievedWidthRatio: 0.78 },
      timingsMs: { total: 1200 },
      debugFiles: ["mask.png"],
    })?.metadata;
    expect(metadata).toMatchObject({
      plateUsed: "front_right_45",
      errorCode: "perspective_mismatch",
      details: {
        qualityGate: { contactRiseRatio: 0.41 },
        placement: { targetWidthRatio: 0.81, achievedWidthRatio: 0.78 },
      },
    });
    expect(Object.keys(metadata?.details ?? {}).sort()).toEqual(["placement", "qualityGate"]);
    expect(job({ plateUsed: "cockpit", errorCode: "Bad Code" })?.metadata).toMatchObject({
      plateUsed: null,
      errorCode: null,
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
    expect(parseProcessorHealth({ status: "ok", showroomSource: "plates", showroomPlaceholder: false })).toMatchObject({
      showroomSource: "plates",
      showroomPlaceholder: false,
    });
    expect(parseProcessorHealth({ status: "ok", showroomSource: "missing" })).toMatchObject({
      showroomSource: "missing",
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
    expect(parseProcessorHealth({ status: "error", showroomSource: "plates" })).toBeNull();
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
  it("builds the proxy URL for the plate of a shot", () => {
    expect(DEV_TEST_API.showroom("autoexperten_standard", "front_left_45")).toBe(
      "/api/dev/processing-test/showroom?preset=autoexperten_standard&shot=front_left_45&width=1600",
    );
    expect(DEV_TEST_API.showroom("autoexperten_standard", "rear", 2048)).toBe(
      "/api/dev/processing-test/showroom?preset=autoexperten_standard&shot=rear&width=2048",
    );
  });
});
