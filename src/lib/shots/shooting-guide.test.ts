import { describe, expect, it } from "vitest";
import {
  DEFAULT_SHOOTING_TIPS_PREFS,
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
  it("opens once per device – for a new vehicle only", () => {
    expect(shouldShowShootingTips(DEFAULT_SHOOTING_TIPS_PREFS, true)).toBe(true);
    // A vehicle with photos is not new.
    expect(shouldShowShootingTips(DEFAULT_SHOOTING_TIPS_PREFS, false)).toBe(false);
    // Shown once: daily use costs no extra tap (the light bulb and "Mehr" still show the tips).
    expect(shouldShowShootingTips({ autoShown: true }, true)).toBe(false);
  });

  it("reads the stored state defensively (also the earlier opt-out)", () => {
    expect(parseShootingTipsPrefs(null)).toEqual(DEFAULT_SHOOTING_TIPS_PREFS);
    expect(parseShootingTipsPrefs("{not json")).toEqual(DEFAULT_SHOOTING_TIPS_PREFS);
    expect(parseShootingTipsPrefs("null")).toEqual(DEFAULT_SHOOTING_TIPS_PREFS);
    expect(parseShootingTipsPrefs('{"autoShown":true}')).toEqual({ autoShown: true });
    expect(parseShootingTipsPrefs('{"autoShow":false,"seenVehicleIds":[]}')).toEqual({ autoShown: true });
    expect(parseShootingTipsPrefs('{"autoShown":"yes"}')).toEqual({ autoShown: false });
  });
});
