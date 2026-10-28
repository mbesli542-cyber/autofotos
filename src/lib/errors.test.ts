import { describe, expect, it } from "vitest";
import { AppError, isRejectedFileError, toUserMessage } from "./errors";

describe("isRejectedFileError", () => {
  it("is true only for files the server refuses for good (type, size)", () => {
    expect(isRejectedFileError(new AppError("unsupported_file"))).toBe(true);
    expect(isRejectedFileError(new AppError("file_too_large"))).toBe(true);
    for (const code of ["network", "unauthorized", "storage", "not_found", "invalid", "unknown"] as const) {
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
