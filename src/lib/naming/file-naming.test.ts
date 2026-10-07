import { describe, expect, it } from "vitest";
import { getOrderedShots, AUTOEXPERTEN_STANDARD_TEMPLATE } from "@/lib/shots/shot-template";
import {
  buildExportFileName,
  buildOriginalStoragePath,
  buildProcessedStoragePath,
  buildThumbnailStoragePath,
  getFileExtensionForMimeType,
  sanitizeFileNameSegment,
} from "./file-naming";

const VEHICLE_ID = "5d0c6f1e-63a1-4c84-9a5f-0d5b3f2c6301";

describe("export file names", () => {
  it("follows AE_{internalReference}_{order}_{shotKey}.jpg", () => {
    expect(
      buildExportFileName({
        internalReference: "FZ-2041",
        vehicleId: VEHICLE_ID,
        shotOrder: 1,
        shotKey: "front_left_45",
      }),
    ).toBe("AE_FZ-2041_01_front_left_45.jpg");
    expect(
      buildExportFileName({ internalReference: "FZ-2041", vehicleId: VEHICLE_ID, shotOrder: 2, shotKey: "front" }),
    ).toBe("AE_FZ-2041_02_front.jpg");
  });

  it("is deterministic and keeps the template order for the full set", () => {
    const names = getOrderedShots(AUTOEXPERTEN_STANDARD_TEMPLATE).map((shot) =>
      buildExportFileName({
        internalReference: "FZ-2041",
        vehicleId: VEHICLE_ID,
        shotOrder: shot.order,
        shotKey: shot.key,
      }),
    );
    expect(names[0]).toBe("AE_FZ-2041_01_front_left_45.jpg");
    expect(names[14]).toBe("AE_FZ-2041_15_special_detail.jpg");
    expect([...names].sort()).toEqual(names); // lexical order == listing order
  });

  it("sanitises internal references (umlauts, spaces, slashes)", () => {
    expect(sanitizeFileNameSegment("Größe 12/ä ß")).toBe("Groesse-12-ae-ss");
    expect(
      buildExportFileName({ internalReference: "  Lager 7/B ", vehicleId: VEHICLE_ID, shotOrder: 7, shotKey: "rear" }),
    ).toBe("AE_Lager-7-B_07_rear.jpg");
  });

  it("falls back to the vehicle id without an internal reference", () => {
    expect(
      buildExportFileName({ internalReference: null, vehicleId: VEHICLE_ID, shotOrder: 3, shotKey: "front_right_45" }),
    ).toBe("AE_5D0C6F1E_03_front_right_45.jpg");
    expect(
      buildExportFileName({ internalReference: "///", vehicleId: VEHICLE_ID, shotOrder: 3, shotKey: "front_right_45" }),
    ).toBe("AE_5D0C6F1E_03_front_right_45.jpg");
  });

  it("supports other extensions", () => {
    expect(
      buildExportFileName({ internalReference: "X1", vehicleId: VEHICLE_ID, shotOrder: 14, shotKey: "wheel_detail", extension: ".png" }),
    ).toBe("AE_X1_14_wheel_detail.png");
  });
});

describe("storage paths", () => {
  const input = { vehicleId: VEHICLE_ID, shotKey: "front", photoId: "p1" };

  it("gives every capture a unique original path (originals are never overwritten)", () => {
    expect(buildOriginalStoragePath({ ...input, extension: "jpg" })).toBe(`${VEHICLE_ID}/front/p1.jpg`);
    expect(buildOriginalStoragePath({ ...input, photoId: "p2", extension: "jpg" })).not.toBe(
      buildOriginalStoragePath({ ...input, extension: "jpg" }),
    );
  });

  it("keeps processed and thumbnail files separate from originals", () => {
    expect(buildThumbnailStoragePath(input)).toBe(`${VEHICLE_ID}/front/p1.jpg`);
    expect(buildProcessedStoragePath({ ...input, preset: "autoexperten_standard" })).toBe(
      `${VEHICLE_ID}/autoexperten_standard/front/p1.jpg`,
    );
  });

  it("maps mime types to extensions", () => {
    expect(getFileExtensionForMimeType("image/jpeg")).toBe("jpg");
    expect(getFileExtensionForMimeType("image/HEIC")).toBe("heic");
    expect(getFileExtensionForMimeType("application/octet-stream")).toBe("jpg");
  });
});
