import { describe, expect, it } from "vitest";
import { cameraHref, cameraReturnHref, parseCameraReturnTarget } from "./camera-links";

describe("camera links", () => {
  it("opens the guided camera for one shot with a return target", () => {
    expect(cameraHref("veh_1")).toBe("/fahrzeuge/veh_1/kamera");
    expect(cameraHref("veh_1", { shot: "front_left_45", returnTo: "fotos" })).toBe(
      "/fahrzeuge/veh_1/kamera?shot=front_left_45&zurueck=fotos",
    );
    expect(cameraHref("veh_1", { shot: "rear", returnTo: "bearbeiten" })).toBe(
      "/fahrzeuge/veh_1/kamera?shot=rear&zurueck=bearbeiten",
    );
  });

  it("accepts only the known return targets", () => {
    expect(parseCameraReturnTarget("fotos")).toBe("fotos");
    expect(parseCameraReturnTarget("bearbeiten")).toBe("bearbeiten");
    for (const value of ["daten", "https://example.com", "", undefined, ["fotos"]]) {
      expect(parseCameraReturnTarget(value)).toBeNull();
    }
  });

  it("returns to review, processing or the vehicle", () => {
    expect(cameraReturnHref("veh_1", "fotos")).toBe("/fahrzeuge/veh_1/fotos");
    expect(cameraReturnHref("veh_1", "bearbeiten")).toBe("/fahrzeuge/veh_1/bearbeiten");
    expect(cameraReturnHref("veh_1", null)).toBe("/fahrzeuge/veh_1");
  });
});
