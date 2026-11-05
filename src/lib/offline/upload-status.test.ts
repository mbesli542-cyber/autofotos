import { describe, expect, it } from "vitest";
import { USER_ERROR_MESSAGES } from "@/lib/errors";
import type { UploadItem } from "./upload-queue";
import { failedUploadsText, storageFullMessage, summarizeFailedUploads, UNSAVED_PHOTOS_LOST_HINT } from "./upload-status";

type Item = Pick<UploadItem, "status" | "retryable" | "storageFull" | "unpersisted">;
const failed = (patch: Partial<Item> = {}): Item => ({ status: "failed", retryable: true, ...patch });

describe("summarizeFailedUploads", () => {
  it("counts failed captures by what helps: retry, discard, deleting vehicles", () => {
    expect(
      summarizeFailedUploads([
        { status: "queued", retryable: true, unpersisted: true },
        { status: "uploading", retryable: true },
        failed(),
        failed({ retryable: false }),
        failed({ storageFull: true, unpersisted: true }),
        failed({ storageFull: true }),
      ]),
    ).toEqual({ retryable: 3, refused: 1, storageFull: 2, unpersisted: 1 });
  });
});

describe("texts", () => {
  it("camera: says the storage is full – and that unsaved photos are lost only when they are in memory only", () => {
    expect(storageFullMessage({ unpersisted: 0 })).toBe(USER_ERROR_MESSAGES.storage_full);
    expect(storageFullMessage({ unpersisted: 2 })).toBe(`${USER_ERROR_MESSAGES.storage_full} ${UNSAVED_PHOTOS_LOST_HINT}`);
  });

  it("vehicle page: never promises the photos stay on the device when they are in memory only", () => {
    expect(failedUploadsText({ retryable: 0, refused: 1, storageFull: 0, unpersisted: 0 })).toBeNull();
    expect(failedUploadsText({ retryable: 1, refused: 0, storageFull: 0, unpersisted: 0 })).toBe(
      "1 Foto konnte noch nicht gespeichert werden. Sie bleiben auf diesem Gerät.",
    );
    expect(failedUploadsText({ retryable: 2, refused: 0, storageFull: 2, unpersisted: 2 })).toBe(
      "2 Fotos konnten nicht gespeichert werden: Der Speicher auf diesem Gerät ist voll. Bitte bereits gespeicherte Fahrzeuge löschen. Nicht gespeicherte Fotos gehen verloren, wenn die App geschlossen wird.",
    );
    expect(failedUploadsText({ retryable: 1, refused: 0, storageFull: 0, unpersisted: 1 })).toBe(
      `1 Foto konnte noch nicht gespeichert werden. ${UNSAVED_PHOTOS_LOST_HINT}`,
    );
  });
});
