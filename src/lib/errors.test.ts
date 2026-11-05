import { describe, expect, it } from "vitest";
import {
  AppError,
  isQuotaExceededError,
  isRejectedFileError,
  isStorageFullError,
  storageError,
  toUserMessage,
} from "./errors";

describe("isRejectedFileError", () => {
  it("is true only for files the server refuses for good (type, size)", () => {
    expect(isRejectedFileError(new AppError("unsupported_file"))).toBe(true);
    expect(isRejectedFileError(new AppError("file_too_large"))).toBe(true);
    for (const code of ["network", "unauthorized", "storage", "storage_full", "not_found", "invalid", "unknown"] as const) {
      expect(isRejectedFileError(new AppError(code))).toBe(false);
    }
    expect(isRejectedFileError(new TypeError("Failed to fetch"))).toBe(false);
  });

  it("has plain German messages", () => {
    expect(toUserMessage(new AppError("unsupported_file"))).toBe(
      "Dieses Bildformat wird nicht unterstützt (JPG, PNG, WebP oder HEIC).",
    );
    expect(toUserMessage(new AppError("file_too_large"))).toBe("Das Foto ist zu groß (max. 50 MB).");
  });
});

describe("storage full (QuotaExceededError)", () => {
  const FULL = "Der Speicher auf diesem Gerät ist voll. Bitte bereits gespeicherte Fahrzeuge löschen.";
  const quota = () => new DOMException("The quota has been exceeded.", "QuotaExceededError");

  it("is recognised directly and as the cause of a wrapped error", () => {
    expect(isQuotaExceededError(quota())).toBe(true);
    expect(isQuotaExceededError(new DOMException("full", "NS_ERROR_DOM_QUOTA_REACHED"))).toBe(true);
    expect(isQuotaExceededError(new AppError("storage", { cause: quota() }))).toBe(true);
    expect(isQuotaExceededError(new Error("outer", { cause: new Error("inner", { cause: quota() }) }))).toBe(true);
    expect(isQuotaExceededError(new DOMException("Aborted", "AbortError"))).toBe(false);
    expect(isQuotaExceededError(new AppError("storage"))).toBe(false);
    expect(isQuotaExceededError(null)).toBe(false);
    // The upload queue's flag: the AppError made from it counts, too.
    expect(isStorageFullError(new AppError("storage_full"))).toBe(true);
    expect(isStorageFullError(quota())).toBe(true);
    expect(isStorageFullError(new AppError("storage"))).toBe(false);
    expect(isStorageFullError(new TypeError("Failed to fetch"))).toBe(false);
  });

  it("tells the employee to delete vehicles instead of retrying", () => {
    expect(toUserMessage(quota())).toBe(FULL);
    expect(toUserMessage(new AppError("storage", { cause: quota() }), "Foto konnte nicht gespeichert werden.")).toBe(FULL);
    expect(storageError(quota()).code).toBe("storage_full");
    expect(toUserMessage(storageError(quota()))).toBe(FULL);
    // Any other storage failure keeps the retry message.
    const other = storageError(new DOMException("x", "UnknownError"));
    expect(other.code).toBe("storage");
    expect(toUserMessage(other)).toBe("Foto konnte nicht gespeichert werden. Bitte versuchen Sie es erneut.");
  });
});
