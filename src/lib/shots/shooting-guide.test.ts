import { describe, expect, it } from "vitest";
import {
  DEFAULT_SHOOTING_TIPS_PREFS,
  markShootingTipsSeen,
  parseShootingTipsPrefs,
  SHOOTING_TIPS,
  shouldShowShootingTips,
} from "./shooting-guide";
import { AUTOEXPERTEN_STANDARD_TEMPLATE, getOrderedShots } from "./shot-template";

describe("SHOOTING_TIPS", () => {
  it("covers the photo protocol (hall, light, washed car, free space, whole car, plate frames, landscape, originals)", () => {
    const text = SHOOTING_TIPS.map((tip) => `${tip.title} ${tip.text}`).join(" ");
    for (const part of ["Halle", "Licht im Rücken", "Sonne", "gewaschen", "2–3 m", "Ganzes Fahrzeug", "Kennzeichenhalter", "Querformat", "WhatsApp"]) {
      expect(text).toContain(part);
    }
    expect(new Set(SHOOTING_TIPS.map((tip) => tip.id)).size).toBe(SHOOTING_TIPS.length);
  });
});

describe("shot template hints", () => {
  const shots = getOrderedShots(AUTOEXPERTEN_STANDARD_TEMPLATE);

  it("gives every exterior shot a short photo hint", () => {
    for (const shot of shots.filter((entry) => entry.category === "exterior")) {
      expect(shot.tip, shot.key).toBeTruthy();
      expect((shot.tip ?? "").length, shot.key).toBeLessThanOrEqual(90);
    }
    expect(shots.find((shot) => shot.key === "front")?.tip).toContain("Kennzeichenhalter");
    expect(shots.find((shot) => shot.key === "front_left_45")?.tip).toContain("2–3 m frei");
  });
});

describe("when the tips open by themselves", () => {
  it("opens once per new vehicle", () => {
    expect(shouldShowShootingTips(DEFAULT_SHOOTING_TIPS_PREFS, "veh-1", true)).toBe(true);
    // A vehicle with photos is not new.
    expect(shouldShowShootingTips(DEFAULT_SHOOTING_TIPS_PREFS, "veh-1", false)).toBe(false);
    const seen = markShootingTipsSeen(DEFAULT_SHOOTING_TIPS_PREFS, "veh-1");
    expect(shouldShowShootingTips(seen, "veh-1", true)).toBe(false);
    expect(shouldShowShootingTips(seen, "veh-2", true)).toBe(true);
  });

  it("never opens by itself again after 'nicht mehr automatisch zeigen'", () => {
    const off = markShootingTipsSeen(DEFAULT_SHOOTING_TIPS_PREFS, "veh-1", false);
    expect(shouldShowShootingTips(off, "veh-2", true)).toBe(false);
    // Marking another vehicle keeps the choice.
    expect(markShootingTipsSeen(off, "veh-3").autoShow).toBe(false);
  });

  it("remembers the last 50 vehicles only", () => {
    let prefs = DEFAULT_SHOOTING_TIPS_PREFS;
    for (let i = 0; i < 60; i += 1) prefs = markShootingTipsSeen(prefs, `veh-${i}`);
    expect(prefs.seenVehicleIds).toHaveLength(50);
    expect(prefs.seenVehicleIds.at(-1)).toBe("veh-59");
    expect(prefs.seenVehicleIds).not.toContain("veh-0");
  });

  it("reads stored choices defensively", () => {
    expect(parseShootingTipsPrefs(null)).toEqual(DEFAULT_SHOOTING_TIPS_PREFS);
    expect(parseShootingTipsPrefs("{not json")).toEqual(DEFAULT_SHOOTING_TIPS_PREFS);
    expect(parseShootingTipsPrefs('{"autoShow":false,"seenVehicleIds":["a",3]}')).toEqual({
      autoShow: false,
      seenVehicleIds: ["a"],
    });
    expect(parseShootingTipsPrefs("null")).toEqual(DEFAULT_SHOOTING_TIPS_PREFS);
  });
});
