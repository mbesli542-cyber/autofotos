import { describe, expect, it } from "vitest";
import type { VehiclePhotoWithUrls } from "@/lib/domain/types";
import type { UploadItem } from "@/lib/offline/upload-queue";
import { AUTOEXPERTEN_STANDARD_TEMPLATE as T } from "@/lib/shots/shot-template";
import { buildPhotoSlots, formatSlotNumber, isSlotCaptured } from "./photo-slots";

const photo = (shotKey: string, shotOrder: number, title = shotKey) =>
  ({
    id: `id-${shotKey}`,
    vehicleId: "v1",
    shotKey,
    shotOrder,
    title,
    urls: { original: "o", thumbnail: "t", processed: null },
  }) as VehiclePhotoWithUrls;

const pendingItem = (shotKey: string, shotOrder: number, status: UploadItem["status"] = "uploading"): UploadItem => ({
  id: `u-${shotKey}`,
  vehicleId: "v1",
  shotKey,
  shotOrder,
  title: shotKey,
  status,
  thumbnailUrl: null,
  error: null,
});

describe("buildPhotoSlots", () => {
  it("creates one slot per template shot in template order", () => {
    const { required, extras } = buildPhotoSlots(T, [photo("rear", 7), photo("front_left_45", 1)]);
    expect(required).toHaveLength(15);
    expect(required.map((s) => s.order)).toEqual(Array.from({ length: 15 }, (_, i) => i + 1));
    expect(required[0]?.photo?.id).toBe("id-front_left_45");
    expect(required[6]?.photo?.id).toBe("id-rear");
    expect(required[1]?.photo).toBeNull();
    expect(extras).toEqual([]);
  });

  it("treats pending uploads as captured (offline queue)", () => {
    const { required } = buildPhotoSlots(T, [], [pendingItem("front", 2, "failed")]);
    const front = required.find((s) => s.key === "front");
    expect(front && isSlotCaptured(front)).toBe(true);
    expect(front?.pending?.status).toBe("failed");
  });

  it("lists additional photos after the template shots", () => {
    const { extras } = buildPhotoSlots(
      T,
      [photo("extra_02", 102, "Zusatzfoto 2"), photo("extra_01", 101, "Zusatzfoto 1")],
      [pendingItem("extra_03", 103)],
    );
    expect(extras.map((s) => s.key)).toEqual(["extra_01", "extra_02", "extra_03"]);
    expect(extras.map((slot) => formatSlotNumber(slot))).toEqual(["+1", "+2", "+3"]);
  });
});
