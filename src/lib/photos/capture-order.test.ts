import { describe, expect, it } from "vitest";
import { isOlderCapture, isOlderThanSavedPhoto, savedCaptureTime } from "./capture-order";

const saved = (takenAt: string, createdAt: string) => ({ takenAt, createdAt });

describe("capture order (the rule of add_vehicle_photo)", () => {
  it("counts a saved photo as taken at most 5 minutes after it was saved", () => {
    // Taken before it was saved: its capture time.
    expect(savedCaptureTime(saved("2026-10-28T10:00:00.000Z", "2026-10-28T11:00:00.000Z"))).toBe(
      Date.parse("2026-10-28T10:00:00.000Z"),
    );
    // Dated a month ahead (camera clock): saved time + 5 minutes.
    expect(savedCaptureTime(saved("2026-11-28T10:00:00.000Z", "2026-10-28T10:00:00.000Z"))).toBe(
      Date.parse("2026-10-28T10:05:00.000Z"),
    );
  });

  it("keeps the saved photo against an older capture, the new capture wins otherwise and on a tie", () => {
    const photo = saved("2026-10-28T10:00:00.000Z", "2026-10-28T10:00:30.000Z");
    expect(isOlderThanSavedPhoto("2026-10-28T09:59:59.999Z", photo)).toBe(true);
    expect(isOlderThanSavedPhoto("2026-10-28T10:00:00.000Z", photo)).toBe(false);
    expect(isOlderThanSavedPhoto("2026-10-28T10:00:00.001Z", photo)).toBe(false);
  });

  it("lets a retake with the real time replace a photo dated ahead – after 5 minutes at most", () => {
    const ahead = saved("2027-01-01T00:00:00.000Z", "2026-10-28T10:00:00.000Z");
    // Within the 5 minutes after it was saved the photo dated ahead stays …
    expect(isOlderThanSavedPhoto("2026-10-28T10:04:59.000Z", ahead)).toBe(true);
    // … from then on a capture with the real time is the newer one.
    expect(isOlderThanSavedPhoto("2026-10-28T10:05:00.000Z", ahead)).toBe(false);
    expect(isOlderThanSavedPhoto("2026-10-28T12:00:00.000Z", ahead)).toBe(false);
  });

  it("never calls a capture older on unreadable times", () => {
    expect(isOlderThanSavedPhoto("kaputt", saved("2026-10-28T10:00:00.000Z", "2026-10-28T10:00:00.000Z"))).toBe(false);
    expect(isOlderThanSavedPhoto("2026-10-28T09:00:00.000Z", saved("kaputt", "2026-10-28T10:00:00.000Z"))).toBe(true);
    expect(isOlderThanSavedPhoto("2026-10-28T09:00:00.000Z", saved("kaputt", "kaputt"))).toBe(false);
    expect(isOlderCapture("kaputt", "2026-10-28T10:00:00.000Z")).toBe(false);
  });

  it("orders two unsaved captures by their capture times", () => {
    expect(isOlderCapture("2026-10-28T10:00:00.000Z", "2026-10-28T10:00:01.000Z")).toBe(true);
    expect(isOlderCapture("2026-10-28T10:00:01.000Z", "2026-10-28T10:00:01.000Z")).toBe(false);
  });
});
