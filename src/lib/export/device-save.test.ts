import { afterEach, describe, expect, it, vi } from "vitest";
import {
  canShareFiles,
  chooseSaveMethod,
  downloadBlob,
  hasFreshTap,
  isTouchDevice,
  SHARE_BATCH_LIMITS,
  ShareFailedError,
  shareFiles,
  splitIntoShareBatches,
} from "./device-save";

const MB = 1024 * 1024;
const file = (name: string, size = 10) => new File([new Uint8Array(size)], name, { type: "image/jpeg" });

afterEach(() => {
  vi.useRealTimers();
});

describe("splitIntoShareBatches", () => {
  it("shares 15 listing photos in two parts of at most 10 (Chrome on Android's limit), in order", () => {
    const photos = Array.from({ length: 15 }, (_, i) => ({ name: String(i + 1), size: 2 * MB }));
    const batches = splitIntoShareBatches(photos);
    expect(batches.map((batch) => batch.length)).toEqual([10, 5]);
    expect(batches.flat().map((photo) => photo.name)).toEqual(photos.map((photo) => photo.name));
  });

  it("keeps every part below the byte limit; a single larger file is a part of its own", () => {
    const sizes = [20, 20, 20, 60, 5].map((mb) => ({ size: mb * MB }));
    const batches = splitIntoShareBatches(sizes);
    expect(batches.map((batch) => batch.map((entry) => entry.size / MB))).toEqual([[20, 20], [20], [60], [5]]);
    for (const batch of batches.filter((entry) => entry.length > 1)) {
      expect(batch.reduce((sum, entry) => sum + entry.size, 0)).toBeLessThanOrEqual(SHARE_BATCH_LIMITS.maxBytes);
    }
  });

  it("gives no part for no files", () => {
    expect(splitIntoShareBatches([])).toEqual([]);
  });
});

describe("chooseSaveMethod", () => {
  it("shares on phones that can share the files, downloads on desktops and otherwise", () => {
    expect(chooseSaveMethod({ touch: true, canShare: true })).toBe("share");
    expect(chooseSaveMethod({ touch: true, canShare: false })).toBe("download");
    expect(chooseSaveMethod({ touch: false, canShare: true })).toBe("download");
  });

  it("recognises phones by a coarse pointer without hover", () => {
    const media = (coarse: boolean, hoverNone: boolean) => ({
      matchMedia: (query: string) =>
        ({ matches: query === "(pointer: coarse)" ? coarse : query === "(hover: none)" ? hoverNone : false }) as MediaQueryList,
    });
    expect(isTouchDevice(media(true, true))).toBe(true);
    expect(isTouchDevice(media(false, false))).toBe(false);
    // A laptop with a touch screen still has a mouse.
    expect(isTouchDevice(media(true, false))).toBe(false);
    expect(isTouchDevice(undefined)).toBe(false);
  });
});

describe("canShareFiles / hasFreshTap", () => {
  it("asks the browser about exactly these files", () => {
    const canShare = vi.fn((data?: ShareData) => (data?.files?.length ?? 0) <= 2);
    const nav = { share: vi.fn(), canShare };
    expect(canShareFiles([file("a.jpg"), file("b.jpg")], nav)).toBe(true);
    expect(canShareFiles([file("a.jpg"), file("b.jpg"), file("c.jpg")], nav)).toBe(false);
    expect(canShareFiles([], nav)).toBe(false);
  });

  it("is false without the Web Share API or when canShare throws", () => {
    expect(canShareFiles([file("a.jpg")], {})).toBe(false);
    expect(canShareFiles([file("a.jpg")], { share: vi.fn(), canShare: () => { throw new TypeError("bad"); } })).toBe(false);
    expect(canShareFiles([file("a.jpg")], undefined)).toBe(false);
  });

  it("knows when the tap has expired", () => {
    expect(hasFreshTap({ userActivation: { isActive: true } })).toBe(true);
    expect(hasFreshTap({ userActivation: { isActive: false } })).toBe(false);
    expect(hasFreshTap({})).toBe(true);
  });
});

describe("shareFiles", () => {
  it("shares the files only (a text would hide 'Bilder sichern' on iOS)", async () => {
    const share = vi.fn(async () => {});
    const files = [file("AE_X_01_front_left_45.jpg"), file("AE_X_02_front.jpg")];
    expect(await shareFiles(files, { share })).toBe("shared");
    expect(share).toHaveBeenCalledWith({ files });
  });

  it("treats closing the share sheet as a cancel, not an error", async () => {
    const share = vi.fn(async () => {
      throw new DOMException("Share canceled", "AbortError");
    });
    expect(await shareFiles([file("a.jpg")], { share })).toBe("cancelled");
  });

  it("reports any other refusal in German (too many files, expired tap, …)", async () => {
    const share = vi.fn(async () => {
      throw new DOMException("Permission denied", "NotAllowedError");
    });
    const error = await shareFiles([file("a.jpg")], { share }).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ShareFailedError);
    expect((error as Error).message).toBe("Das Teilen hat nicht funktioniert. Bitte die Fotos stattdessen herunterladen.");
    await expect(shareFiles([file("a.jpg")], {})).rejects.toBeInstanceOf(ShareFailedError);
  });
});

describe("downloadBlob", () => {
  it("downloads under the given file name and frees the URL afterwards", () => {
    vi.useFakeTimers();
    const created: string[] = [];
    const revoked: string[] = [];
    URL.createObjectURL = vi.fn(() => {
      created.push("blob:zip");
      return "blob:zip";
    });
    URL.revokeObjectURL = vi.fn((url: string) => revoked.push(url));
    const clicked: { download: string; href: string }[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push({ download: this.download, href: this.href });
    });

    downloadBlob(new Blob(["zip"], { type: "application/zip" }), "AE_FZ-2041_Fotos.zip");

    expect(clicked).toEqual([{ download: "AE_FZ-2041_Fotos.zip", href: "blob:zip" }]);
    expect(document.querySelector("a[download]")).toBeNull();
    expect(revoked).toEqual([]);
    vi.advanceTimersByTime(60_000);
    expect(revoked).toEqual(created);
    click.mockRestore();
  });
});
