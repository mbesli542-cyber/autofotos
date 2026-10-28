import { describe, expect, it } from "vitest";
import { checkPhotoFile, getPhotoFileType, PHOTO_FILE_ACCEPT, PHOTO_FILE_MAX_BYTES } from "./photo-file";

const UNSUPPORTED = "Dieses Bildformat wird nicht unterstützt (JPG, PNG, WebP oder HEIC).";
const TOO_LARGE = "Das Foto ist zu groß (max. 50 MB).";

function file(name: string, type: string, size?: number): File {
  const result = new File(["x"], name, { type, lastModified: 1_760_000_000_000 });
  if (size !== undefined) Object.defineProperty(result, "size", { value: size });
  return result;
}

describe("photo files", () => {
  it("accepts exactly the formats of the originals storage", () => {
    for (const type of ["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"]) {
      const picked = file("foto", type);
      expect(checkPhotoFile(picked)).toEqual({ ok: true, file: picked });
    }
  });

  it("offers no HEIC in the file picker, so iOS converts library photos to JPEG", () => {
    // Listing image/heic(f) makes iOS Safari upload the HEIC original, which
    // Chrome, Firefox and Android cannot display.
    expect(PHOTO_FILE_ACCEPT).toBe("image/jpeg,image/png,image/webp");
    expect(PHOTO_FILE_ACCEPT).not.toMatch(/heic|heif/);
  });

  it("rejects other images and non-images with a German message", () => {
    for (const [name, type] of [
      ["foto.avif", "image/avif"],
      ["foto.gif", "image/gif"],
      ["foto.tiff", "image/tiff"],
      ["foto.bmp", "image/bmp"],
      ["foto.svg", "image/svg+xml"],
      ["foto.dng", "image/x-adobe-dng"],
      ["liste.pdf", "application/pdf"],
      ["foto", ""],
      ["foto.avif", ""],
    ] as const) {
      expect(checkPhotoFile(file(name, type))).toEqual({ ok: false, message: UNSUPPORTED });
    }
  });

  it("takes an unknown type from the extension and normalises image/jpg", () => {
    expect(getPhotoFileType({ name: "IMG_0042.HEIC", type: "" })).toBe("image/heic");
    expect(getPhotoFileType({ name: "scan.heif", type: "application/octet-stream" })).toBe("image/heif");
    expect(getPhotoFileType({ name: "foto.JPEG", type: "" })).toBe("image/jpeg");
    expect(getPhotoFileType({ name: "foto.jpg", type: "image/jpg" })).toBe("image/jpeg");
    expect(getPhotoFileType({ name: "foto.jpg", type: "image/pjpeg" })).toBe("image/jpeg");
    expect(getPhotoFileType({ name: "foto.png", type: "IMAGE/PNG" })).toBe("image/png");
    // A known (other) type is never overridden by the extension.
    expect(getPhotoFileType({ name: "foto.jpg", type: "image/gif" })).toBeNull();

    const heic = checkPhotoFile(file("IMG_0042.HEIC", ""));
    expect(heic.ok && heic.file.type).toBe("image/heic");
    const jpg = checkPhotoFile(file("foto.jpg", "image/jpg"));
    expect(jpg.ok && [jpg.file.type, jpg.file.name, jpg.file.lastModified]).toEqual([
      "image/jpeg",
      "foto.jpg",
      1_760_000_000_000,
    ]);
  });

  it("rejects photos over 50 MB", () => {
    expect(checkPhotoFile(file("foto.jpg", "image/jpeg", PHOTO_FILE_MAX_BYTES)).ok).toBe(true);
    expect(checkPhotoFile(file("foto.jpg", "image/jpeg", PHOTO_FILE_MAX_BYTES + 1))).toEqual({ ok: false, message: TOO_LARGE });
    expect(checkPhotoFile(file("foto.heic", "", 60 * 1024 * 1024))).toEqual({ ok: false, message: TOO_LARGE });
  });
});
