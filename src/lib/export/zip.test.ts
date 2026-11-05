import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { buildZipBlob, crc32OfBlob } from "./zip-blob";
import {
  crc32,
  createStoredZip,
  planStoredZip,
  toDosDateTime,
  ZIP_MAX_32,
  ZipError,
  type ZipEntryMeta,
} from "./zip";

const text = (value: string) => new TextEncoder().encode(value);
const MODIFIED = new Date(2026, 10, 5, 14, 37, 58); // local time

/** Minimal reader: every entry as the central directory and its local header describe it. */
function readZip(archive: Uint8Array) {
  const view = new DataView(archive.buffer, archive.byteOffset, archive.byteLength);
  const end = archive.length - 22;
  expect(view.getUint32(end, true)).toBe(0x06054b50);
  const count = view.getUint16(end + 10, true);
  const centralSize = view.getUint32(end + 12, true);
  let position = view.getUint32(end + 16, true);
  expect(position + centralSize).toBe(end);
  const entries = [];
  for (let i = 0; i < count; i += 1) {
    expect(view.getUint32(position, true)).toBe(0x02014b50);
    const flags = view.getUint16(position + 8, true);
    const method = view.getUint16(position + 10, true);
    const time = view.getUint16(position + 12, true);
    const date = view.getUint16(position + 14, true);
    const crc = view.getUint32(position + 16, true);
    const size = view.getUint32(position + 24, true);
    const nameLength = view.getUint16(position + 28, true);
    const extraLength = view.getUint16(position + 30, true);
    const commentLength = view.getUint16(position + 32, true);
    const offset = view.getUint32(position + 42, true);
    const name = new TextDecoder().decode(archive.subarray(position + 46, position + 46 + nameLength));
    position += 46 + nameLength + extraLength + commentLength;

    expect(view.getUint32(offset, true)).toBe(0x04034b50);
    expect(view.getUint16(offset + 6, true)).toBe(flags);
    expect(view.getUint32(offset + 14, true)).toBe(crc);
    expect(view.getUint32(offset + 18, true)).toBe(size);
    const localName = view.getUint16(offset + 26, true);
    const localExtra = view.getUint16(offset + 28, true);
    const start = offset + 30 + localName + localExtra;
    const data = archive.subarray(start, start + size);
    entries.push({ name, flags, method, time, date, crc, data });
  }
  return entries;
}

describe("crc32", () => {
  it("matches the standard check value and the empty input", () => {
    expect(crc32(text("123456789"))).toBe(0xcbf43926);
    expect(crc32(new Uint8Array())).toBe(0);
    expect(crc32(text("The quick brown fox jumps over the lazy dog"))).toBe(0x414fa339);
  });

  it("can be computed in pieces", () => {
    const whole = text("AutoExperten Schwetzingen – Showroom");
    expect(crc32(whole.subarray(10), crc32(whole.subarray(0, 10)))).toBe(crc32(whole));
  });
});

describe("toDosDateTime", () => {
  it("stores local date and time in 2-second steps", () => {
    const { date, time } = toDosDateTime(MODIFIED);
    expect(date >> 9).toBe(2026 - 1980);
    expect((date >> 5) & 0xf).toBe(11);
    expect(date & 0x1f).toBe(5);
    expect(time >> 11).toBe(14);
    expect((time >> 5) & 0x3f).toBe(37);
    expect((time & 0x1f) * 2).toBe(58);
  });

  it("clamps dates outside 1980–2107 and invalid dates", () => {
    expect(toDosDateTime(new Date(1975, 0, 1))).toEqual({ date: (1 << 5) | 1, time: 0 });
    expect(toDosDateTime(new Date(Number.NaN))).toEqual({ date: (1 << 5) | 1, time: 0 });
    expect(toDosDateTime(new Date(2200, 0, 1)).date >> 9).toBe(127);
  });
});

describe("createStoredZip", () => {
  const files = [
    { name: "AE_FZ-2041_01_front_left_45.jpg", data: text("\xff\xd8 first photo"), modified: MODIFIED },
    { name: "AE_FZ-2041_02_front.jpg", data: new Uint8Array(0), modified: MODIFIED },
    { name: "Übersicht/Grüße ß.jpg", data: text("umlauts"), modified: MODIFIED },
  ];

  it("writes every entry stored, in order, with CRC-32, sizes and UTF-8 names", () => {
    const entries = readZip(createStoredZip(files));
    expect(entries.map((entry) => entry.name)).toEqual(files.map((file) => file.name));
    for (const [index, entry] of entries.entries()) {
      const file = files[index];
      if (!file) throw new Error("missing file");
      expect(entry.method).toBe(0);
      expect(entry.flags & 0x0800).toBe(0x0800);
      expect(entry.crc).toBe(crc32(file.data));
      expect(Array.from(entry.data)).toEqual(Array.from(file.data));
      expect(entry).toMatchObject(toDosDateTime(MODIFIED));
    }
  });

  it("writes a valid empty archive", () => {
    expect(readZip(createStoredZip([]))).toEqual([]);
  });

  it.each([
    "",
    "/absolute.jpg",
    "../outside.jpg",
    "folder/../../outside.jpg",
    "back\\slash.jpg",
    "folder/",
    "nul\u0000.jpg",
  ])("refuses the unsafe entry name %j", (name) => {
    expect(() => createStoredZip([{ name, data: text("x") }])).toThrow(ZipError);
  });

  it("refuses two entries with the same name (also differing only in case)", () => {
    const error = (() => {
      try {
        createStoredZip([
          { name: "AE_1_01_front.jpg", data: text("a") },
          { name: "ae_1_01_FRONT.jpg", data: text("b") },
        ]);
      } catch (caught) {
        return caught;
      }
    })();
    expect(error).toBeInstanceOf(ZipError);
    expect((error as ZipError).code).toBe("duplicate_name");
  });
});

describe("planStoredZip – no ZIP64", () => {
  const meta = (name: string, size: number): ZipEntryMeta => ({ name, size, crc32: 0, modified: MODIFIED });

  it("refuses an entry of 4 GB or more", () => {
    expect(() => planStoredZip([meta("big.jpg", ZIP_MAX_32 + 1)])).toThrow(
      expect.objectContaining({ code: "too_large" }),
    );
  });

  it("refuses an archive that would reach 4 GB in total", () => {
    expect(() => planStoredZip([meta("a.jpg", 2_500_000_000), meta("b.jpg", 2_500_000_000)])).toThrow(
      expect.objectContaining({ code: "too_large" }),
    );
  });

  it("accepts a large archive below the limit (planned from sizes, no bytes needed)", () => {
    const plan = planStoredZip([meta("a.jpg", 2_000_000_000), meta("b.jpg", 2_000_000_000)]);
    expect(plan.size).toBeLessThan(2 ** 32);
    expect(plan.parts.filter((part) => !(part instanceof Uint8Array))).toEqual([{ entry: 0 }, { entry: 1 }]);
  });

  it("refuses more than 65 534 entries", () => {
    const metas = Array.from({ length: 65_535 }, (_, i) => meta(`${i}.jpg`, 0));
    expect(() => planStoredZip(metas)).toThrow(expect.objectContaining({ code: "too_many_entries" }));
  });

  it("refuses invalid sizes and CRCs", () => {
    expect(() => planStoredZip([meta("a.jpg", -1)])).toThrow(ZipError);
    expect(() => planStoredZip([{ ...meta("a.jpg", 1), crc32: 2 ** 32 }])).toThrow(ZipError);
  });
});

describe("buildZipBlob", () => {
  it("produces the same archive as createStoredZip from Blobs", async () => {
    const photos = [
      { name: "AE_X_01_front_left_45.jpg", data: text("one") },
      { name: "AE_X_02_front.jpg", data: text("two two") },
    ];
    const blob = buildZipBlob(
      await Promise.all(
        photos.map(async (photo) => {
          const part = new Blob([photo.data as Uint8Array<ArrayBuffer>], { type: "image/jpeg" });
          return { name: photo.name, blob: part, crc32: await crc32OfBlob(part), modified: MODIFIED };
        }),
      ),
    );
    expect(blob.type).toBe("application/zip");
    const bytes = new Uint8Array(await blob.arrayBuffer());
    expect(Array.from(bytes)).toEqual(Array.from(createStoredZip(photos.map((p) => ({ ...p, modified: MODIFIED })))));
  });

  it("computes the CRC of a Blob across its read chunks", async () => {
    const data = new Uint8Array(4 * 1024 * 1024 + 123);
    for (let i = 0; i < data.length; i += 1) data[i] = (i * 31 + (i >> 7)) & 0xff;
    expect(await crc32OfBlob(new Blob([data]))).toBe(crc32(data));
  });
});

function hasTool(command: string, args: string[]): boolean {
  try {
    execFileSync(command, args, { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

describe("the archive in standard tools", () => {
  const python = hasTool("python3", ["--version"]);
  const unzip = hasTool("unzip", ["-v"]);

  function withArchive(check: (path: string) => void) {
    const dir = mkdtempSync(join(tmpdir(), "ae-zip-"));
    try {
      const path = join(dir, "AE_FZ-2041_Fotos.zip");
      writeFileSync(
        path,
        createStoredZip([
          { name: "AE_FZ-2041_01_front_left_45.jpg", data: text("\xff\xd8 photo one"), modified: MODIFIED },
          { name: "AE_FZ-2041_02_front.jpg", data: text("\xff\xd8 photo two"), modified: MODIFIED },
          { name: "Grüße.jpg", data: text("utf-8 name"), modified: MODIFIED },
        ]),
      );
      check(path);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  }

  it.skipIf(!python)("passes python3 -m zipfile -t", () => {
    withArchive((path) => {
      const output = execFileSync("python3", ["-I", "-m", "zipfile", "-t", path], { encoding: "utf8" });
      expect(output).toContain("Done testing");
      const names = execFileSync("python3", ["-I", "-m", "zipfile", "-l", path], { encoding: "utf8" });
      expect(names).toContain("Grüße.jpg");
    });
  });

  it.skipIf(!unzip)("passes unzip -t", () => {
    withArchive((path) => {
      const output = execFileSync("unzip", ["-t", path], { encoding: "utf8" });
      expect(output).toContain("No errors detected");
    });
  });
});
