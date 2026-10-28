import { describe, expect, it } from "vitest";
import { getSlotImageUrl } from "@/components/photos/ShotThumbnail";
import type { VehiclePhotoWithUrls } from "@/lib/domain/types";
import type { UploadItem } from "@/lib/offline/upload-queue";
import { AUTOEXPERTEN_STANDARD_TEMPLATE as T } from "@/lib/shots/shot-template";
import { buildPhotoSlots, formatSlotNumber, isSlotCaptured } from "./photo-slots";

const EARLIER = "2026-10-28T10:00:00.000Z";
const LATER = "2026-10-28T10:00:30.000Z";

const photo = (shotKey: string, shotOrder: number, title = shotKey, takenAt = EARLIER) =>
  ({
    id: `id-${shotKey}`,
    vehicleId: "v1",
    shotKey,
    shotOrder,
    title,
    takenAt,
    urls: { original: "o", thumbnail: "t", processed: "p" },
  }) as VehiclePhotoWithUrls;

const pendingItem = (
  shotKey: string,
  shotOrder: number,
  status: UploadItem["status"] = "uploading",
  takenAt = LATER,
  id = `u-${shotKey}`,
): UploadItem => ({
  id,
  vehicleId: "v1",
  shotKey,
  shotOrder,
  title: shotKey,
  takenAt,
  status,
  thumbnailUrl: `blob:${id}`,
  error: null,
  retryable: true,
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

describe("buildPhotoSlots – saved photo vs. pending capture of the same shot", () => {
  it("shows a pending retake (newer than the saved photo) instead of the saved photo", () => {
    const { required } = buildPhotoSlots(T, [photo("rear", 7, "Hinten", EARLIER)], [pendingItem("rear", 7, "uploading", LATER)]);
    const rear = required.find((s) => s.key === "rear");
    expect(rear?.pending?.id).toBe("u-rear");
    expect(rear && getSlotImageUrl(rear, "original")).toBe("blob:u-rear");
    expect(rear && getSlotImageUrl(rear, "processed")).toBeNull();
  });

  it("lets a newer saved photo win over an older failed capture", () => {
    // The first capture failed to upload, the retake was saved.
    const { required } = buildPhotoSlots(T, [photo("rear", 7, "Hinten", LATER)], [pendingItem("rear", 7, "failed", EARLIER)]);
    const rear = required.find((s) => s.key === "rear");
    expect(rear?.photo?.id).toBe("id-rear");
    expect(rear?.pending).toBeNull();
    expect(rear && getSlotImageUrl(rear, "original")).toBe("t");
    expect(rear && getSlotImageUrl(rear, "processed")).toBe("p");
  });

  it("uses the newest of several pending captures, whatever their queue order", () => {
    const { required } = buildPhotoSlots(T, [], [
      pendingItem("rear", 7, "queued", LATER, "u-new"),
      pendingItem("rear", 7, "failed", EARLIER, "u-old"),
    ]);
    expect(required.find((s) => s.key === "rear")?.pending?.id).toBe("u-new");
  });

  it("does the same for additional photos", () => {
    const { extras } = buildPhotoSlots(
      T,
      [photo("extra_01", 101, "Zusatzfoto 1", LATER)],
      [pendingItem("extra_01", 101, "failed", EARLIER)],
    );
    expect(extras.map((slot) => [slot.key, slot.photo?.id, slot.pending])).toEqual([["extra_01", "id-extra_01", null]]);
  });
});
