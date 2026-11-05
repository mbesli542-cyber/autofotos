import { describe, expect, it } from "vitest";
import {
  describeProcessorConnection,
  getProcessorConnection,
  PROCESSING_OFFLINE_MESSAGE,
  PROCESSOR_UNREACHABLE_MESSAGE,
  type ProcessorConnectionState,
} from "./processor-connection";
import { PROCESSING_NOT_CONNECTED_MESSAGE, type ProcessingStatus } from "./types";

function status(overrides: Partial<ProcessingStatus> = {}): ProcessingStatus {
  return {
    connected: false,
    processor: "real",
    showroomSource: null,
    showroomError: null,
    connectionError: null,
    accessCodeRequired: false,
    dataBackend: "demo",
    ...overrides,
  };
}

describe("getProcessorConnection", () => {
  const ready = { phase: "ready" as const, online: true, uiHint: "real" as const };

  it("says offline whenever the device has no connection", () => {
    expect(getProcessorConnection({ ...ready, status: status({ connected: true }), online: false })).toBe("offline");
    expect(getProcessorConnection({ phase: "error", status: null, online: false, uiHint: "mock" })).toBe("offline");
    expect(getProcessorConnection({ phase: "loading", status: null, online: false, uiHint: "real" })).toBe("offline");
  });

  it("tells a configured processor that does not answer from one that was never set up", () => {
    expect(getProcessorConnection({ ...ready, status: status() })).toBe("unreachable");
    expect(getProcessorConnection({ ...ready, status: status({ processor: "mock" }) })).toBe("not_configured");
    expect(getProcessorConnection({ ...ready, status: status({ connected: true }) })).toBe("connected");
    expect(
      getProcessorConnection({ ...ready, status: status({ connectionError: "Bitte den Administrator informieren." }) }),
    ).toBe("misconfigured");
  });

  it("never says 'not connected' when the status request itself failed", () => {
    expect(getProcessorConnection({ phase: "error", status: null, online: true, uiHint: "mock" })).toBe("unreachable");
    expect(getProcessorConnection({ phase: "error", status: null, online: true, uiHint: "real" })).toBe("unreachable");
  });

  it("uses the build hint only while the first answer is on its way", () => {
    expect(getProcessorConnection({ phase: "loading", status: null, online: true, uiHint: "real" })).toBe("checking");
    expect(getProcessorConnection({ phase: "loading", status: null, online: true, uiHint: "mock" })).toBe("not_configured");
  });
});

describe("describeProcessorConnection", () => {
  const ALL: ProcessorConnectionState[] = ["checking", "connected", "offline", "unreachable", "misconfigured", "not_configured"];

  it("has the daily-use texts", () => {
    expect(describeProcessorConnection("offline").message).toBe(
      "Keine Internetverbindung – die Bearbeitung ist wieder möglich, sobald Sie online sind.",
    );
    expect(PROCESSING_OFFLINE_MESSAGE).toBe(describeProcessorConnection("offline").message);
    expect(describeProcessorConnection("unreachable").message).toBe(
      "Der Showroom-Rechner ist gerade nicht erreichbar. Bitte später erneut versuchen.",
    );
    expect(PROCESSOR_UNREACHABLE_MESSAGE).toBe(describeProcessorConnection("unreachable").message);
    expect(describeProcessorConnection("misconfigured", "Bitte den Administrator informieren.").message).toBe(
      "Bitte den Administrator informieren.",
    );
  });

  it("keeps 'noch nicht verbunden' for a deployment without processing only", () => {
    expect(describeProcessorConnection("not_configured").message).toBe(PROCESSING_NOT_CONNECTED_MESSAGE);
    for (const state of ALL.filter((entry) => entry !== "not_configured")) {
      const text = describeProcessorConnection(state);
      expect(`${text.label} ${text.message ?? ""} ${text.detail ?? ""}`).not.toMatch(/nicht verbunden/);
    }
  });

  it("offers 'Erneut prüfen' where asking again can help", () => {
    expect(describeProcessorConnection("offline").canRetry).toBe(true);
    expect(describeProcessorConnection("unreachable").canRetry).toBe(true);
    expect(describeProcessorConnection("not_configured").canRetry).toBe(false);
  });

  it("never uses the technical word 'Prozessor'", () => {
    for (const state of ALL) {
      const text = describeProcessorConnection(state);
      expect(`${text.label} ${text.message ?? ""} ${text.detail ?? ""}`).not.toMatch(/prozessor/i);
    }
  });
});
