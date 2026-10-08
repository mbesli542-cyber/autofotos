import { describe, expect, it } from "vitest";
import { DEFAULT_SHOT_TEMPLATE } from "@/lib/shots/shot-template";
import { countByTreatment, getShotTreatment, selectPhotosForProcessing } from "./shot-treatment";

const EXTERIOR = [
  "front_left_45",
  "front",
  "front_right_45",
  "left_side",
  "right_side",
  "rear_left_45",
  "rear",
  "rear_right_45",
];

describe("shot treatment", () => {
  it("puts exactly the eight exterior shots into the showroom", () => {
    for (const key of EXTERIOR) expect(getShotTreatment(DEFAULT_SHOT_TEMPLATE, key)).toBe("showroom");
    const others = DEFAULT_SHOT_TEMPLATE.shots.filter((shot) => !EXTERIOR.includes(shot.key));
    expect(others).toHaveLength(7);
    for (const shot of others) {
      expect(getShotTreatment(DEFAULT_SHOT_TEMPLATE, shot.key)).toBe("original_environment");
    }
  });

  it("does not process extra photos", () => {
    expect(getShotTreatment(DEFAULT_SHOT_TEMPLATE, "extra_01")).toBeNull();
    const photos = [{ shotKey: "front" }, { shotKey: "extra_01" }, { shotKey: "cockpit" }];
    expect(selectPhotosForProcessing(DEFAULT_SHOT_TEMPLATE, photos)).toEqual([{ shotKey: "front" }, { shotKey: "cockpit" }]);
  });

  it("counts photos per treatment", () => {
    const photos = DEFAULT_SHOT_TEMPLATE.shots.map((shot) => ({ shotKey: shot.key }));
    expect(countByTreatment(DEFAULT_SHOT_TEMPLATE, [...photos, { shotKey: "extra_01" }])).toEqual({
      showroom: 8,
      original_environment: 7,
    });
  });
});
