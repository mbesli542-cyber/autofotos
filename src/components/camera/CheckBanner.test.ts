import { describe, expect, it } from "vitest";
import { ROTATE_PROMPT } from "@/lib/shots/shooting-guide";
import { checkStatusContent, ROTATE_STATUS_ID, type CheckBannerState } from "./CheckBanner";

const SHOT = { key: "front" };
const REJECTED = { kind: "retake" as const, reason: "Das Fahrzeug ist angeschnitten." };

describe("checkStatusContent (the camera header's one status line)", () => {
  it("shows a banner first, then – briefly – the request to turn the phone, then the shot's own state", () => {
    const banner: CheckBannerState = {
      id: "b1",
      shotKey: "",
      kind: "storage_full",
      label: "Speicher voll – Foto nicht gespeichert",
      detail: "Der Speicher auf diesem Gerät ist voll.",
    };
    expect(checkStatusContent(banner, SHOT, REJECTED, true, true)).toMatchObject({ id: "banner:b1", tone: "retake" });
    expect(checkStatusContent(null, SHOT, REJECTED, true, true)).toEqual({
      id: ROTATE_STATUS_ID,
      tone: "hint",
      label: ROTATE_PROMPT.label,
      detail: ROTATE_PROMPT.detail,
      retakeShotKey: null,
    });
    expect(checkStatusContent(null, SHOT, REJECTED, true, false)).toMatchObject({
      tone: "retake",
      label: "Bitte neu aufnehmen",
      detail: REJECTED.reason,
    });
    expect(checkStatusContent(null, SHOT, { kind: "none" }, false, false)).toBeNull();
  });
});
