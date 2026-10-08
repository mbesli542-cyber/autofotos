import { describe, expect, it } from "vitest";
import { planUpload } from "./prepare-upload";

const MB = 1_000_000;

describe("planUpload", () => {
  it("sends small JPEG/PNG/WebP originals unchanged (the processor applies EXIF orientation)", () => {
    expect(planUpload({ size: 2 * MB, type: "image/jpeg" }, { width: 3200, height: 2400 })).toBe("send_original");
    expect(planUpload({ size: 1 * MB, type: "image/png" }, { width: 1600, height: 1200 })).toBe("send_original");
  });

  it("re-encodes large or oversized photos", () => {
    expect(planUpload({ size: 3 * MB, type: "image/jpeg" }, { width: 4032, height: 3024 })).toBe("reencode");
    expect(planUpload({ size: 6 * MB, type: "image/jpeg" }, { width: 3000, height: 2000 })).toBe("reencode");
  });

  it("re-encodes formats the processor may not read (e.g. HEIC) when the browser can decode them", () => {
    expect(planUpload({ size: 1 * MB, type: "image/heic" }, { width: 3000, height: 2000 })).toBe("reencode");
  });

  it("sends undecodable photos unchanged only when they are small enough", () => {
    expect(planUpload({ size: 3.9 * MB, type: "image/heic" }, null)).toBe("send_original");
    expect(planUpload({ size: 4.1 * MB, type: "image/heic" }, null)).toBe("too_large");
    expect(planUpload({ size: 0, type: "image/jpeg" }, null)).toBe("too_large");
  });
});
