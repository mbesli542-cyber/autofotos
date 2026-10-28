import { describe, expect, it } from "vitest";
import { buildSeedData } from "@/lib/data/mock/seed";
import { PROCESSING_BLOCKER_MESSAGES, getProcessingBlocker, isPlaceholderPhoto } from "./run-blockers";

const photo = (originalStoragePath: string) => ({ originalStoragePath });
const OWN = photo("vehicle-originals/veh_1/front/p1.jpg");

describe("getProcessingBlocker", () => {
  it("keeps the demo sample vehicle (placeholder drawings) out of processing", () => {
    const sample = buildSeedData().photos.filter((entry) => entry.vehicleId.startsWith("5d0c6f1e"));
    expect(sample).toHaveLength(15);
    expect(sample.every(isPlaceholderPhoto)).toBe(true);
    expect(getProcessingBlocker(sample, [])).toBe("sample_vehicle");
    expect(getProcessingBlocker([OWN, ...sample.slice(0, 1)], [])).toBe("sample_vehicle");
    expect(PROCESSING_BLOCKER_MESSAGES.sample_vehicle).toBe("Beispielfahrzeug – bitte ein eigenes Fahrzeug fotografieren.");
  });

  it("waits for captures that are still being saved, not for failed uploads", () => {
    expect(getProcessingBlocker([OWN], [{ status: "queued" }])).toBe("uploads_pending");
    expect(getProcessingBlocker([OWN], [{ status: "failed" }, { status: "uploading" }])).toBe("uploads_pending");
    expect(getProcessingBlocker([OWN], [{ status: "failed" }])).toBeNull();
    expect(getProcessingBlocker([OWN], [])).toBeNull();
  });
});
