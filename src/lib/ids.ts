/**
 * Random ids (RFC 4122 version 4 UUIDs) for client-generated records.
 *
 * crypto.randomUUID() only exists in secure contexts (HTTPS, localhost). When
 * the app is opened over plain HTTP in the LAN, the id is built from
 * crypto.getRandomValues(), which is available everywhere.
 */
export function createId(): string {
  const webCrypto = globalThis.crypto;
  if (typeof webCrypto.randomUUID === "function") return webCrypto.randomUUID();

  const bytes = webCrypto.getRandomValues(new Uint8Array(16));
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40; // version 4
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80; // RFC 4122 variant
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
