import { describe, expect, it } from "vitest";
import {
  getWarningKind,
  hasRetakeWarning,
  infoWarnings,
  needsResultCheck,
  parseProcessingWarnings,
  warningsToCheck,
} from "./processing-warnings";

const warning = (code: string) => ({ code, message: `Hinweis ${code}` });

describe("processor hints on a result", () => {
  it("knows every hint the processor reports (processor/app/pipeline: mask.py, pipeline.py)", () => {
    expect(getWarningKind("plate_mirrored")).toBe("retake");
    expect(getWarningKind("background_object_suspected")).toBe("retake");
    for (const code of [
      "background_object_removed",
      "vehicle_occluded",
      "mask_uncertain",
      "busy_background",
      "vehicle_not_detected",
    ]) {
      expect(getWarningKind(code)).toBe("check");
    }
    for (const code of ["low_resolution", "vehicle_reduced", "vehicle_small"]) expect(getWarningKind(code)).toBe("info");
  });

  it("never hides an unknown hint: it asks for a check", () => {
    expect(getWarningKind("new_code_2027")).toBe("check");
    expect(getWarningKind("constructor")).toBe("check");
    expect(needsResultCheck([warning("new_code_2027")])).toBe(true);
  });

  it("separates hints to check (retakes first) from information", () => {
    const hints = [warning("low_resolution"), warning("mask_uncertain"), warning("plate_mirrored"), warning("vehicle_small")];
    expect(warningsToCheck(hints).map((hint) => hint.code)).toEqual(["plate_mirrored", "mask_uncertain"]);
    expect(infoWarnings(hints).map((hint) => hint.code)).toEqual(["low_resolution", "vehicle_small"]);
    expect(needsResultCheck([warning("low_resolution"), warning("vehicle_reduced")])).toBe(false);
    expect(hasRetakeWarning(hints)).toBe(true);
    expect(hasRetakeWarning([warning("mask_uncertain")])).toBe(false);
  });

  it("keeps only usable hints from untrusted data", () => {
    expect(parseProcessingWarnings(null)).toEqual([]);
    expect(parseProcessingWarnings({ code: "x", message: "y" })).toEqual([]);
    expect(
      parseProcessingWarnings([
        { code: "mask_uncertain", message: "  Die Freistellung ist unsicher – bitte das Ergebnis prüfen.  " },
        { code: 3, message: "Ohne Code" },
        { code: "empty", message: "   " },
        "Text",
        null,
      ]),
    ).toEqual([
      { code: "mask_uncertain", message: "Die Freistellung ist unsicher – bitte das Ergebnis prüfen." },
      { code: "warning", message: "Ohne Code" },
    ]);
    expect(parseProcessingWarnings(Array.from({ length: 30 }, () => warning("mask_uncertain")))).toHaveLength(20);
  });
});

describe("parseProcessingWarnings – what the database can store, without hiding a hint", () => {
  /** Size of the hints as PostgreSQL measures them: its JSON text adds ": " / ", " spaces (≤ 4 bytes per hint). */
  const storedBytes = (hints: readonly unknown[]) =>
    new TextEncoder().encode(JSON.stringify(hints)).length + 4 * hints.length;
  const DB_LIMIT_BYTES = 16_384; // vehicle_photos_processing_warnings_check
  const words = (count: number, word = "Kontur") => Array.from({ length: count }, () => word).join(" ");

  it("shortens an over-long hint instead of dropping it", () => {
    const long = `Ein Gegenstand ragte in die Fahrzeugkontur ${words(50)} – bitte das Ergebnis prüfen.`;
    const [hint] = parseProcessingWarnings([{ code: "background_object_removed", message: long }]);
    expect(hint?.code).toBe("background_object_removed");
    expect(Array.from(hint?.message ?? "").length).toBeLessThanOrEqual(300);
    expect(hint?.message).toMatch(/^Ein Gegenstand ragte in die Fahrzeugkontur Kontur/);
    // Cut at a word, marked as shortened.
    expect(hint?.message).toMatch(/ Kontur…$/);
    expect(parseProcessingWarnings([{ code: "x", message: "x".repeat(301) }])[0]?.message).toBe(`${"x".repeat(299)}…`);
  });

  it("replaces control characters (a NUL cannot be stored) and never splits a character", () => {
    expect(parseProcessingWarnings([{ code: "mask\u0000uncertain", message: "Bitte\u0000das Ergebnis\nprüfen.\u0007" }])).toEqual([
      { code: "mask uncertain", message: "Bitte das Ergebnis prüfen." },
    ]);
    const [hint] = parseProcessingWarnings([{ code: "x", message: "🚗".repeat(400) }]);
    expect(hint?.message).toBe(`${"🚗".repeat(299)}…`);
    expect(hint?.message.isWellFormed()).toBe(true);
  });

  it("stays within the database's bound – hints to check or retake are kept before information", () => {
    const big = (code: string, index: number) => ({ code, message: `${index}: ${"€".repeat(290)}` });
    const input = [
      ...Array.from({ length: 10 }, (_, index) => big("low_resolution", index)),
      ...Array.from({ length: 10 }, (_, index) => big("background_object_removed", 10 + index)),
      big("plate_mirrored", 20),
    ];
    const kept = parseProcessingWarnings(input);
    expect(storedBytes(kept)).toBeLessThanOrEqual(12_000 + 4 * kept.length);
    expect(kept.filter((hint) => hint.code !== "low_resolution")).toHaveLength(11);
    expect(kept.filter((hint) => hint.code === "low_resolution").length).toBeGreaterThan(0);
    // In the processor's order.
    expect(kept.map((hint) => Number.parseInt(hint.message, 10))).toEqual(
      [...kept.map((hint) => Number.parseInt(hint.message, 10))].sort((a, b) => a - b),
    );
  });

  it("never yields more than the database accepts, whatever the processor sends", () => {
    const worst = Array.from({ length: 40 }, (_, index) => ({
      code: `${"c".repeat(60)}${index}`,
      message: "\u{1F697}".repeat(500), // 4 bytes per character
    }));
    const kept = parseProcessingWarnings(worst);
    expect(kept.length).toBeGreaterThan(0);
    expect(kept.length).toBeLessThanOrEqual(20);
    expect(storedBytes(kept)).toBeLessThan(DB_LIMIT_BYTES);
    const many = parseProcessingWarnings(Array.from({ length: 40 }, (_, index) => ({ code: "mask_uncertain", message: `${index} ${"ä".repeat(298)}` })));
    expect(storedBytes(many)).toBeLessThan(DB_LIMIT_BYTES);
  });

  it("changes nothing when parsing parsed hints", () => {
    const input = [
      { code: `${"x".repeat(39)} y`, message: `${words(70)}` },
      { code: "mask_uncertain", message: `${words(70)}` },
      { code: "low_resolution", message: "Das Fahrzeug musste vergrößert werden." },
      ...Array.from({ length: 25 }, (_, index) => ({ code: `x${index}`, message: "€".repeat(320) })),
    ];
    const once = parseProcessingWarnings(input);
    expect(parseProcessingWarnings(once)).toEqual(once);
  });
});
