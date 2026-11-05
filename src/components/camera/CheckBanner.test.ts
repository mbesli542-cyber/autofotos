import { describe, expect, it } from "vitest";
import { ROTATE_PROMPT } from "@/lib/shots/shooting-guide";
import { checkStatusContent, ROTATE_STATUS_ID, SHOT_TIP_LABEL, type CheckBannerState } from "./CheckBanner";

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

  it("shows the shot's photo hint until the shot is captured – in the header, not in the viewfinder", () => {
    const shot = { key: "cockpit", tip: "Lenkrad gerade, Innenraum aufgeräumt – keine persönlichen Gegenstände." };
    expect(checkStatusContent(null, shot, { kind: "none" }, false, false)).toEqual({
      id: "tip:cockpit",
      tone: "tip",
      label: SHOT_TIP_LABEL,
      detail: shot.tip,
      retakeShotKey: null,
    });
    // The request to turn the phone and a finished check come first; a captured shot shows its state.
    expect(checkStatusContent(null, shot, { kind: "none" }, false, true)?.id).toBe(ROTATE_STATUS_ID);
    expect(checkStatusContent(null, shot, { kind: "saved" }, true, false)).toMatchObject({ label: "Gespeichert" });
    expect(checkStatusContent(null, { key: "rear_seats", tip: null }, { kind: "none" }, false, false)).toBeNull();
  });
});
