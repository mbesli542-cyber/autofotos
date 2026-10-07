import { describe, expect, it } from "vitest";
import {
  AUTOEXPERTEN_STANDARD_TEMPLATE,
  getNextExtraShot,
  getOrderedShots,
  getRequiredShots,
  isExtraShotKey,
} from "./shot-template";

describe("AutoExperten standard shot template", () => {
  const shots = getOrderedShots(AUTOEXPERTEN_STANDARD_TEMPLATE);

  it("contains exactly 15 required shots in the agreed order", () => {
    expect(getRequiredShots(AUTOEXPERTEN_STANDARD_TEMPLATE)).toHaveLength(15);
    expect(shots.map((shot) => shot.key)).toEqual([
      "front_left_45",
      "front",
      "front_right_45",
      "left_side",
      "right_side",
      "rear_left_45",
      "rear",
      "rear_right_45",
      "cockpit",
      "front_interior",
      "rear_seats",
      "driver_seat",
      "door_controls",
      "wheel_detail",
      "special_detail",
    ]);
  });

  it("uses consecutive orders 1..15 and unique keys", () => {
    expect(shots.map((shot) => shot.order)).toEqual(Array.from({ length: 15 }, (_, i) => i + 1));
    expect(new Set(shots.map((shot) => shot.key)).size).toBe(15);
  });

  it("has German titles and the required instructions", () => {
    expect(shots[0]).toMatchObject({
      title: "Vorne links (45°)",
      instruction: "Positionieren Sie das Fahrzeug wie in der Vorlage.",
      category: "exterior",
    });
    expect(shots.find((s) => s.key === "cockpit")?.instruction).toBe(
      "Fotografieren Sie das Cockpit möglichst symmetrisch.",
    );
    expect(shots.find((s) => s.key === "special_detail")?.category).toBe("detail");
  });

  it("has a framing overlay for every exterior shot", () => {
    for (const shot of shots.filter((s) => s.category === "exterior")) {
      expect(shot.overlayAsset).toMatch(/^\/overlays\/.+\.svg$/);
    }
    expect(shots.find((s) => s.key === "right_side")?.overlayMirrored).toBe(true);
  });
});

describe("getNextExtraShot", () => {
  it("starts at extra_01 after all template shots", () => {
    expect(getNextExtraShot(["front", "rear"])).toEqual({
      key: "extra_01",
      order: 101,
      title: "Zusatzfoto 1",
    });
  });

  it("continues after the highest existing extra photo", () => {
    expect(getNextExtraShot(["extra_01", "extra_03"]).key).toBe("extra_04");
    expect(isExtraShotKey("extra_04")).toBe(true);
    expect(isExtraShotKey("front")).toBe(false);
  });
});
