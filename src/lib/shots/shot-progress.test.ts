import { describe, expect, it } from "vitest";
import {
  countCapturedRequired,
  getAdjacentShotKey,
  getCapturedShotKeys,
  getFirstMissingShotKey,
  getMissingRequiredShots,
  getNextShotKeyAfterCapture,
  getShotProgress,
  isShotSetComplete,
  sortPhotosByShotOrder,
} from "./shot-progress";
import { AUTOEXPERTEN_STANDARD_TEMPLATE as T, getOrderedShots } from "./shot-template";

const ALL_KEYS = getOrderedShots(T).map((shot) => shot.key);
const keys = (...list: string[]) => new Set(list);

describe("required shot completion", () => {
  it("is incomplete without photos", () => {
    expect(isShotSetComplete(T, keys())).toBe(false);
    expect(getMissingRequiredShots(T, keys())).toHaveLength(15);
    expect(getShotProgress(T, keys())).toMatchObject({ captured: 0, required: 15, remaining: 15 });
  });

  it("is complete only with all 15 required shots", () => {
    const fourteen = new Set(ALL_KEYS.slice(0, 14));
    expect(isShotSetComplete(T, fourteen)).toBe(false);
    expect(getMissingRequiredShots(T, fourteen).map((s) => s.key)).toEqual(["special_detail"]);
    expect(isShotSetComplete(T, new Set(ALL_KEYS))).toBe(true);
    expect(getShotProgress(T, new Set(ALL_KEYS))).toMatchObject({ isComplete: true, ratio: 1 });
  });

  it("ignores extra photos when counting required shots", () => {
    const captured = keys("front", "extra_01", "extra_02");
    expect(countCapturedRequired(T, captured)).toBe(1);
  });

  it("derives captured keys from photos", () => {
    expect(getCapturedShotKeys([{ shotKey: "rear", shotOrder: 7 }])).toEqual(keys("rear"));
  });
});

describe("shot ordering", () => {
  it("sorts by template order, never by capture time", () => {
    const photos = [
      { shotKey: "rear", shotOrder: 7, takenAt: "2026-10-07T08:00:00Z" },
      { shotKey: "extra_01", shotOrder: 101, takenAt: "2026-10-07T07:00:00Z" },
      { shotKey: "front_left_45", shotOrder: 1, takenAt: "2026-10-07T09:00:00Z" },
      { shotKey: "cockpit", shotOrder: 9, takenAt: "2026-10-07T06:00:00Z" },
    ];
    expect(sortPhotosByShotOrder(T, photos).map((p) => p.shotKey)).toEqual([
      "front_left_45",
      "rear",
      "cockpit",
      "extra_01",
    ]);
  });

  it("uses the template order even if a stored shot_order is wrong", () => {
    const photos = [
      { shotKey: "front", shotOrder: 99 },
      { shotKey: "front_left_45", shotOrder: 50 },
    ];
    expect(sortPhotosByShotOrder(T, photos).map((p) => p.shotKey)).toEqual(["front_left_45", "front"]);
  });
});

describe("guided navigation", () => {
  it("starts at the first missing shot", () => {
    expect(getFirstMissingShotKey(T, keys())).toBe("front_left_45");
    expect(getFirstMissingShotKey(T, keys("front_left_45", "front"))).toBe("front_right_45");
    expect(getFirstMissingShotKey(T, new Set(ALL_KEYS))).toBeNull();
  });

  it("advances to the next missing shot after a capture", () => {
    expect(getNextShotKeyAfterCapture(T, keys("front_left_45"), "front_left_45")).toBe("front");
    // skips shots that already have a photo
    expect(getNextShotKeyAfterCapture(T, keys("front_left_45", "front", "front_right_45"), "front_left_45")).toBe(
      "left_side",
    );
  });

  it("wraps around to earlier gaps and ends when everything is captured", () => {
    const allButFront = new Set(ALL_KEYS.filter((key) => key !== "front"));
    expect(getNextShotKeyAfterCapture(T, allButFront, "special_detail")).toBe("front");
    expect(getNextShotKeyAfterCapture(T, new Set(ALL_KEYS), "front")).toBeNull();
  });

  it("finds previous and next shots without wrapping", () => {
    expect(getAdjacentShotKey(T, "front", -1)).toBe("front_left_45");
    expect(getAdjacentShotKey(T, "front", 1)).toBe("front_right_45");
    expect(getAdjacentShotKey(T, "front_left_45", -1)).toBeNull();
    expect(getAdjacentShotKey(T, "special_detail", 1)).toBeNull();
  });
});
