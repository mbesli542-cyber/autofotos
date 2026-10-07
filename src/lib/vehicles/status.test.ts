import { describe, expect, it } from "vitest";
import { AUTOEXPERTEN_STANDARD_TEMPLATE as T, getOrderedShots } from "@/lib/shots/shot-template";
import { canCompleteCapture, canProcessVehicle, deriveStatusAfterPhotoChange } from "./status";

const allPhotos = (processed = false) =>
  getOrderedShots(T).map((shot) => ({
    shotKey: shot.key,
    shotOrder: shot.order,
    processedStoragePath: processed ? `v/p/${shot.key}.jpg` : null,
  }));

describe("vehicle status rules", () => {
  it("is new without photos and capturing while shots are missing", () => {
    expect(deriveStatusAfterPhotoChange("capturing", T, [])).toBe("new");
    expect(deriveStatusAfterPhotoChange("new", T, allPhotos().slice(0, 3))).toBe("capturing");
    expect(deriveStatusAfterPhotoChange("complete", T, allPhotos().slice(0, 14))).toBe("capturing");
  });

  it("does not mark a set complete automatically", () => {
    expect(deriveStatusAfterPhotoChange("capturing", T, allPhotos())).toBe("capturing");
    expect(deriveStatusAfterPhotoChange("complete", T, allPhotos())).toBe("complete");
  });

  it("drops from processed to complete when a photo was retaken", () => {
    expect(deriveStatusAfterPhotoChange("processed", T, allPhotos(true))).toBe("processed");
    const retaken = allPhotos(true).map((p, i) => (i === 0 ? { ...p, processedStoragePath: null } : p));
    expect(deriveStatusAfterPhotoChange("processed", T, retaken)).toBe("complete");
  });

  it("only allows completing with all required shots", () => {
    expect(canCompleteCapture(T, allPhotos().slice(0, 14))).toBe(false);
    expect(canCompleteCapture(T, allPhotos())).toBe(true);
  });

  it("only allows processing after completion", () => {
    expect(canProcessVehicle("capturing")).toBe(false);
    expect(canProcessVehicle("complete")).toBe(true);
    expect(canProcessVehicle("processed")).toBe(true);
  });
});
