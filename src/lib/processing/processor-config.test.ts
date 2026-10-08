import { describe, expect, it } from "vitest";
import { resolveImageProcessorConfig } from "./processor-config";

describe("resolveImageProcessorConfig", () => {
  it("is not connected by default and with IMAGE_PROCESSOR=mock", () => {
    expect(resolveImageProcessorConfig({})).toEqual({ kind: "mock", reason: "disabled" });
    expect(
      resolveImageProcessorConfig({ IMAGE_PROCESSOR: "mock", IMAGE_PROCESSING_API_URL: "http://localhost:8000" }),
    ).toEqual({ kind: "mock", reason: "disabled" });
  });

  it("connects the real processor with URL and optional key", () => {
    expect(
      resolveImageProcessorConfig({
        IMAGE_PROCESSOR: "real",
        IMAGE_PROCESSING_API_URL: " https://host/processor ",
        IMAGE_PROCESSING_API_KEY: " secret ",
      }),
    ).toEqual({ kind: "real", baseUrl: "https://host/processor", apiKey: "secret" });
    expect(
      resolveImageProcessorConfig({ IMAGE_PROCESSOR: "real", IMAGE_PROCESSING_API_URL: "http://127.0.0.1:8000" }),
    ).toEqual({ kind: "real", baseUrl: "http://127.0.0.1:8000", apiKey: null });
  });

  it("stays disconnected when the URL is missing or invalid", () => {
    expect(resolveImageProcessorConfig({ IMAGE_PROCESSOR: "real" })).toEqual({
      kind: "mock",
      reason: "missing_url",
    });
    expect(resolveImageProcessorConfig({ IMAGE_PROCESSOR: "real", IMAGE_PROCESSING_API_URL: "ftp://x" })).toEqual({
      kind: "mock",
      reason: "invalid_url",
    });
    expect(resolveImageProcessorConfig({ IMAGE_PROCESSOR: "real", IMAGE_PROCESSING_API_URL: "not a url" })).toEqual({
      kind: "mock",
      reason: "invalid_url",
    });
  });
});
