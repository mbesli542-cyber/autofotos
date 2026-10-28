import { afterEach, describe, expect, it, vi } from "vitest";
import { createId } from "./ids";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("createId", () => {
  it("uses crypto.randomUUID when available", () => {
    const randomUUID = vi.spyOn(globalThis.crypto, "randomUUID");
    expect(createId()).toMatch(UUID_V4);
    expect(randomUUID).toHaveBeenCalledOnce();
  });

  it("builds a v4 UUID from getRandomValues in insecure contexts (plain HTTP in the LAN)", () => {
    // WebCrypto's randomUUID is [SecureContext]: it is missing over http://192.168.x.x.
    const { crypto } = globalThis;
    vi.stubGlobal("crypto", { getRandomValues: crypto.getRandomValues.bind(crypto) });
    expect(globalThis.crypto.randomUUID).toBeUndefined();

    const ids = new Set(Array.from({ length: 200 }, () => createId()));
    expect(ids.size).toBe(200);
    for (const id of ids) expect(id).toMatch(UUID_V4);
  });
});
