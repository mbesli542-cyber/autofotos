import { describe, expect, it } from "vitest";
import { pickedFileTakenAt } from "./capture-time";

const NOW = Date.parse("2026-10-28T12:00:00.000Z");

describe("capture times", () => {
  it("dates a picked file by its date, but never in the future", () => {
    expect(pickedFileTakenAt({ lastModified: Date.parse("2026-05-01T08:00:00.000Z") }, NOW)).toBe(
      "2026-05-01T08:00:00.000Z",
    );
    expect(pickedFileTakenAt({ lastModified: NOW + 30 * 86_400_000 }, NOW)).toBe("2026-10-28T12:00:00.000Z");
    expect(pickedFileTakenAt({ lastModified: 0 }, NOW)).toBe("2026-10-28T12:00:00.000Z");
  });
});
