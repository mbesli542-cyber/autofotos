/**
 * SupabaseDataProvider against a minimal fake client (no network): what is
 * sent to Storage / the Data API and how its answers are mapped.
 */
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SavePhotoInput } from "@/lib/data/types";
import type { VehiclePhoto } from "@/lib/domain/types";
import { AppError, isRejectedFileError, toUserMessage, VehicleChangedError } from "@/lib/errors";
import { PHOTO_COLUMNS, rowToPhoto, rowToVehicle, type VehiclePhotoRow, type VehicleRow } from "./mappers";
import { SupabaseDataProvider } from "./supabase-data-provider";

const VEHICLE_ID = "5d0c6f1e-63a1-4c84-9a5f-0d5b3f2c6301";

/** Shaped like storage-js' StorageApiError. */
function storageError(message: string, status: number, statusCode = String(status), code?: string) {
  return Object.assign(new Error(message), { name: "StorageApiError", status, statusCode, code });
}

function vehicleRow(index: number): VehicleRow & { vehicle_photos: VehiclePhotoRow[] } {
  const id = `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`;
  return {
    id,
    user_id: null,
    manufacturer: "VW",
    model: `Golf ${index}`,
    color: null,
    license_plate: null,
    vin: null,
    mileage: null,
    first_registration: null,
    internal_reference: null,
    notes: null,
    status: "new",
    created_at: "2026-10-28T10:00:00+00:00",
    updated_at: "2026-10-28T10:00:00+00:00",
    vehicle_photos: [],
  };
}

/** The cursor of a keyset page: `created_at.lt."c",and(created_at.eq."c",id.gt.ID)`. */
const CURSOR_PATTERN = /^created_at\.lt\."([^"]+)",and\(created_at\.eq\."([^"]+)",id\.gt\.([0-9a-f-]+)\)$/;

type ListRow = ReturnType<typeof vehicleRow>;

/** Rows after the cursor, in the list order (created_at desc, id asc) – like PostgreSQL. */
function afterCursor(rows: readonly ListRow[], filter: string | null): ListRow[] {
  const sorted = [...rows].sort(
    (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
  );
  if (filter === null) return sorted;
  const match = CURSOR_PATTERN.exec(filter);
  if (!match || match[1] !== match[2]) throw new Error(`unexpected cursor ${filter}`);
  const createdAt = Date.parse(match[1] ?? "");
  const id = match[3] ?? "";
  return sorted.filter((row) => Date.parse(row.created_at) < createdAt || (Date.parse(row.created_at) === createdAt && row.id > id));
}

function fakeClient({
  vehicles = [] as ListRow[],
  maxRows = 1000,
  // PostgREST sends the total in Content-Range when asked (Prefer: count=exact).
  answersCount = true,
  ignoresCursor = false,
} = {}) {
  const calls = {
    signed: [] as { bucket: string; paths: string[]; expiresIn: number; at: number }[],
    uploads: [] as { bucket: string; path: string; body: Blob }[],
    /** Per list request: the keyset cursor (null: first page) and the page size asked for. */
    pages: [] as { cursor: string | null; limit: number | null }[],
    orders: [] as string[],
    /** update() calls: table, values, filters. */
    updates: [] as { table: string; values: Record<string, unknown>; filters: [string, unknown][] }[],
  };
  const state = { uploadError: null as unknown, vehicles };

  const storage = {
    from: (bucket: string) => ({
      async createSignedUrls(paths: string[], expiresIn: number) {
        calls.signed.push({ bucket, paths, expiresIn, at: Date.now() });
        const n = calls.signed.length;
        return { data: paths.map((path) => ({ path, signedUrl: `https://storage/${bucket}/${path}?sig=${n}`, error: null })), error: null };
      },
      async upload(path: string, body: Blob) {
        calls.uploads.push({ bucket, path, body });
        return state.uploadError ? { data: null, error: state.uploadError } : { data: { path }, error: null };
      },
    }),
  };

  function vehiclesQuery() {
    let limit: number | null = null;
    let cursor: string | null = null;
    let counted = false;
    const query = {
      select: (_columns: string, options?: { count?: string }) => {
        counted = options?.count === "exact";
        return query;
      },
      is: () => query,
      order: (column: string, options?: { ascending?: boolean }) => {
        calls.orders.push(`${column} ${options?.ascending === false ? "desc" : "asc"}`);
        return query;
      },
      limit: (rows: number) => {
        limit = rows;
        return query;
      },
      or: (filter: string) => {
        cursor = filter;
        return query;
      },
      then(resolve: (value: unknown) => void) {
        calls.pages.push({ cursor, limit });
        const matching = afterCursor(state.vehicles, ignoresCursor ? null : cursor);
        // PostgREST answers at most max-rows rows.
        const page = matching.slice(0, Math.min(limit ?? Infinity, maxRows));
        resolve({ data: page, error: null, count: counted && answersCount ? matching.length : null });
      },
    };
    return query;
  }

  /** update(values).eq(…)….select() / await – rows of `state.vehicles` or an empty answer. */
  function updateQuery(table: string, values: Record<string, unknown>) {
    const filters: [string, unknown][] = [];
    calls.updates.push({ table, values, filters });
    const answer = () => {
      if (table !== "vehicles") return { data: null, error: null };
      const rows = state.vehicles.filter((row) =>
        filters.every(([column, value]) => (row as unknown as Record<string, unknown>)[column] === value),
      );
      for (const row of rows) Object.assign(row, values, { updated_at: "2026-10-28T12:00:00.123456+00:00" });
      return { data: rows, error: null };
    };
    const query = {
      eq: (column: string, value: unknown) => {
        filters.push([column, value]);
        return query;
      },
      select: () => ({ then: (resolve: (value: unknown) => void) => resolve(answer()) }),
      then: (resolve: (value: unknown) => void) => resolve(answer()),
    };
    return query;
  }

  const client = {
    storage,
    from: (table: string) => {
      if (table !== "vehicles" && table !== "vehicle_photos") throw new Error(`unexpected table ${table}`);
      return {
        ...(table === "vehicles" ? vehiclesQuery() : {}),
        update: (values: Record<string, unknown>) => updateQuery(table, values),
      };
    },
    rpc: (_name: string, args: Record<string, unknown>) => ({
      single: async () => ({
        data: {
          id: args.p_id,
          vehicle_id: args.p_vehicle_id,
          shot_key: args.p_shot_key,
          shot_order: args.p_shot_order,
          title: args.p_title,
          original_storage_path: args.p_original_storage_path,
          processed_storage_path: null,
          processed_preset: null,
          processing_warnings: null,
          thumbnail_storage_path: args.p_thumbnail_storage_path,
          width: args.p_width,
          height: args.p_height,
          taken_at: args.p_taken_at,
          archived_at: null,
          created_at: args.p_taken_at,
          updated_at: args.p_taken_at,
        },
        error: null,
      }),
    }),
  };
  return { client: client as unknown as SupabaseClient, calls, state };
}

function capture(file: Blob): SavePhotoInput {
  return {
    photoId: "11111111-1111-4111-8111-111111111111",
    vehicleId: VEHICLE_ID,
    shotKey: "front",
    shotOrder: 2,
    title: "Vorne",
    file,
    thumbnail: null,
    width: 4000,
    height: 3000,
    takenAt: "2026-10-28T10:00:00.000Z",
  };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("SupabaseDataProvider.savePhoto – what reaches Storage", () => {
  it("sends the normalised MIME type in the body (storage-js ignores the contentType option for Blobs)", async () => {
    const { client, calls } = fakeClient();
    const provider = new SupabaseDataProvider(client);
    await provider.savePhoto(capture(new Blob(["jpeg"], { type: "image/jpg" })));
    const [upload] = calls.uploads;
    expect(upload?.path).toBe(`${VEHICLE_ID}/front/11111111-1111-4111-8111-111111111111.jpg`);
    expect(upload?.body.type).toBe("image/jpeg");
    expect(await upload?.body.text()).toBe("jpeg");
  });

  it("keeps a standard type and sends the original Blob unchanged", async () => {
    const { client, calls } = fakeClient();
    const file = new Blob(["heic"], { type: "image/heic" });
    await new SupabaseDataProvider(client).savePhoto(capture(file));
    expect(calls.uploads[0]?.body).toBe(file);
  });
});

describe("SupabaseDataProvider.savePhoto – files Storage refuses for good", () => {
  async function attempt(error: unknown) {
    const { client, state } = fakeClient();
    state.uploadError = error;
    return new SupabaseDataProvider(client)
      .savePhoto(capture(new Blob(["x"], { type: "image/avif" })))
      .then(() => null, (thrown: unknown) => thrown);
  }

  it.each([
    ["HTTP 415 invalid_mime_type", storageError("mime type image/avif is not supported", 415, "415", "InvalidMimeType")],
    ["older Storage: HTTP 400, statusCode 415", storageError("mime type image/avif is not supported", 400, "415")],
  ])("an unsupported type (%s) is final with a clear message", async (_, error) => {
    const thrown = await attempt(error);
    expect(thrown).toBeInstanceOf(AppError);
    expect(isRejectedFileError(thrown)).toBe(true);
    expect(toUserMessage(thrown)).toBe("Dieses Bildformat wird nicht unterstützt (JPG, PNG, WebP oder HEIC).");
  });

  it.each([
    ["HTTP 413 EntityTooLarge", storageError("The object exceeded the maximum allowed size", 413, "413", "EntityTooLarge")],
    ["older Storage: HTTP 400, statusCode 413", storageError("The object exceeded the maximum allowed size", 400, "413")],
    ["a proxy's 413 without JSON body", storageError("Payload Too Large", 413)],
  ])("a file that is too large (%s) is final with a clear message", async (_, error) => {
    const thrown = await attempt(error);
    expect(isRejectedFileError(thrown)).toBe(true);
    expect(toUserMessage(thrown)).toBe("Das Foto ist zu groß (max. 50 MB).");
  });

  it.each([
    ["401 (session expired)", storageError("Invalid JWT", 401, "401")],
    ["403 (row-level security)", storageError("new row violates row-level security policy", 403, "403")],
    ["500", storageError("Internal Server Error", 500)],
    ["a network failure", new TypeError("Failed to fetch")],
  ])("%s stays retryable", async (_, error) => {
    const thrown = await attempt(error);
    expect(thrown).toBeInstanceOf(AppError);
    expect(isRejectedFileError(thrown)).toBe(false);
  });
});

describe("SupabaseDataProvider signed URLs", () => {
  const photo: VehiclePhoto = {
    id: "p1",
    vehicleId: VEHICLE_ID,
    shotKey: "front",
    shotOrder: 2,
    title: "Vorne",
    originalStoragePath: `${VEHICLE_ID}/front/p1.jpg`,
    processedStoragePath: null,
    processedPreset: null,
    processingWarnings: [],
    thumbnailStoragePath: `${VEHICLE_ID}/front/p1.jpg`,
    width: null,
    height: null,
    takenAt: "2026-10-28T09:00:00.000Z",
    createdAt: "2026-10-28T09:00:00.000Z",
    updatedAt: "2026-10-28T09:00:00.000Z",
  };

  it("never hands out a URL with less than 90 minutes of validity left", async () => {
    // Pages keep URLs in state; lazily loaded images request them much later.
    vi.useFakeTimers({ now: new Date("2026-10-28T09:00:00.000Z"), toFake: ["Date"] });
    const { client, calls } = fakeClient();
    const provider = new SupabaseDataProvider(client);
    const start = Date.now();

    for (let minutes = 0; minutes <= 600; minutes += 1) {
      vi.setSystemTime(start + minutes * 60_000);
      const [resolved] = await provider.resolvePhotoUrls([photo]);
      for (const url of [resolved?.urls.original, resolved?.urls.thumbnail]) {
        const signedWith = calls.signed[Number(new URL(url ?? "").searchParams.get("sig")) - 1];
        if (!signedWith) throw new Error(`unknown URL ${url}`);
        expect(signedWith.expiresIn).toBe(2 * 60 * 60);
        const expiresAt = signedWith.at + signedWith.expiresIn * 1000;
        expect(expiresAt - Date.now()).toBeGreaterThanOrEqual(90 * 60_000);
      }
    }
  });

  it("reuses a URL during its first 30 minutes, then signs it again", async () => {
    vi.useFakeTimers({ now: new Date("2026-10-28T09:00:00.000Z"), toFake: ["Date"] });
    const { client, calls } = fakeClient();
    const provider = new SupabaseDataProvider(client);
    const start = Date.now();
    const [first] = await provider.resolvePhotoUrls([photo]);
    vi.setSystemTime(start + 30 * 60_000);
    const [again] = await provider.resolvePhotoUrls([photo]);
    expect(again?.urls.original).toBe(first?.urls.original);
    expect(calls.signed.map((call) => call.bucket)).toEqual(["vehicle-originals", "vehicle-thumbnails"]);

    vi.setSystemTime(start + 31 * 60_000);
    const [later] = await provider.resolvePhotoUrls([photo]);
    expect(later?.urls.original).not.toBe(first?.urls.original);
    expect(calls.signed).toHaveLength(4);
  });
});

describe("SupabaseDataProvider.updateVehicle – on the data it is based on", () => {
  const input = {
    manufacturer: "VW",
    model: "Golf 1",
    color: "Rot",
    licensePlate: null,
    vin: null,
    mileage: null,
    firstRegistration: null,
    internalReference: null,
    notes: null,
  };

  it("updates only while updated_at is still the one the input is based on", async () => {
    const row = vehicleRow(1);
    const { client, calls } = fakeClient({ vehicles: [row] });
    const saved = await new SupabaseDataProvider(client).updateVehicle(row.id, input, {
      expectedUpdatedAt: "2026-10-28T10:00:00+00:00",
    });
    expect(saved.color).toBe("Rot");
    expect(calls.updates[0]?.filters).toEqual([
      ["id", row.id],
      ["updated_at", "2026-10-28T10:00:00+00:00"],
    ]);
  });

  it("saves nothing and reports the change when the vehicle was changed meanwhile", async () => {
    const row = { ...vehicleRow(1), updated_at: "2026-10-28T11:30:00.654321+00:00" };
    const { client } = fakeClient({ vehicles: [row] });
    const provider = new SupabaseDataProvider(client);
    vi.spyOn(provider, "getVehicle").mockResolvedValue(rowToVehicle(row));
    const error = await provider
      .updateVehicle(row.id, input, { expectedUpdatedAt: "2026-10-28T10:00:00+00:00" })
      .catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(VehicleChangedError);
    expect(toUserMessage(error)).toBe("Die Fahrzeugdaten wurden inzwischen geändert. Bitte prüfen und erneut speichern.");
    expect(row.color).toBeNull();
  });

  it("reports a vehicle that is gone as not found", async () => {
    const { client } = fakeClient({ vehicles: [] });
    const provider = new SupabaseDataProvider(client);
    vi.spyOn(provider, "getVehicle").mockResolvedValue(null);
    const error = await provider
      .updateVehicle(vehicleRow(1).id, input, { expectedUpdatedAt: "2026-10-28T10:00:00+00:00" })
      .catch((reason: unknown) => reason);
    expect(error).toMatchObject({ code: "not_found" });
  });
});

describe("SupabaseDataProvider – the processor's hints on a processed version", () => {
  const removed = {
    code: "background_object_removed",
    message: "Ein Gegenstand hinter dem Fahrzeug … wurde entfernt – bitte das Ergebnis prüfen.",
  };

  it("writes them with the processed result, replacing those of an earlier processing", async () => {
    const { client, calls } = fakeClient();
    const provider = new SupabaseDataProvider(client);
    await provider.recordProcessedPhoto({
      photoId: "p1",
      preset: "autoexperten_standard",
      processedStoragePath: `${VEHICLE_ID}/autoexperten_standard/front/p1.jpg`,
      warnings: [removed],
    });
    await provider.recordProcessedPhoto({
      photoId: "p1",
      preset: "autoexperten_standard",
      processedStoragePath: `${VEHICLE_ID}/autoexperten_standard/front/p1.jpg`,
      warnings: [],
    });
    expect(calls.updates.map((update) => [update.table, update.values.processing_warnings, update.filters])).toEqual([
      ["vehicle_photos", [removed], [["id", "p1"]]],
      ["vehicle_photos", [], [["id", "p1"]]],
    ]);
  });

  it("writes no more than the column accepts (≤ 20 hints, far below its 16 KB)", async () => {
    const { client, calls } = fakeClient();
    await new SupabaseDataProvider(client).recordProcessedPhoto({
      photoId: "p1",
      preset: "autoexperten_standard",
      processedStoragePath: `${VEHICLE_ID}/autoexperten_standard/front/p1.jpg`,
      warnings: Array.from({ length: 30 }, (_, index) => ({ code: "mask_uncertain", message: `${index} ${"ü".repeat(500)}` })),
    });
    const written = calls.updates[0]?.values.processing_warnings as unknown[];
    expect(written.length).toBeGreaterThan(0);
    expect(written.length).toBeLessThanOrEqual(20);
    expect(new TextEncoder().encode(JSON.stringify(written)).length).toBeLessThanOrEqual(12_000);
  });

  it("reads them back only for a photo with a processed version, dropping anything unusable", () => {
    const row: VehiclePhotoRow = {
      id: "p1",
      vehicle_id: VEHICLE_ID,
      shot_key: "front",
      shot_order: 2,
      title: "Vorne",
      original_storage_path: `${VEHICLE_ID}/front/p1.jpg`,
      processed_storage_path: `${VEHICLE_ID}/autoexperten_standard/front/p1.jpg`,
      processed_preset: "autoexperten_standard",
      processing_warnings: [removed, { code: "x" }, "Text"],
      thumbnail_storage_path: null,
      width: null,
      height: null,
      taken_at: "2026-10-28T09:00:00+00:00",
      archived_at: null,
      created_at: "2026-10-28T09:00:00+00:00",
      updated_at: "2026-10-28T09:00:00+00:00",
    };
    expect(rowToPhoto(row).processingWarnings).toEqual([removed]);
    expect(rowToPhoto({ ...row, processing_warnings: null }).processingWarnings).toEqual([]);
    expect(rowToPhoto({ ...row, processed_storage_path: null }).processingWarnings).toEqual([]);
    expect(PHOTO_COLUMNS.split(", ")).toContain("processing_warnings");
  });
});

describe("SupabaseDataProvider.listVehicles", () => {
  const after = (row: ListRow) => `created_at.lt."${row.created_at}",and(created_at.eq."${row.created_at}",id.gt.${row.id})`;

  it("reads every vehicle in pages instead of stopping at the Data API's max-rows limit", async () => {
    const vehicles = Array.from({ length: 1200 }, (_, index) => vehicleRow(index));
    const { client, calls } = fakeClient({ vehicles, maxRows: 1000 });
    const list = await new SupabaseDataProvider(client).listVehicles();
    expect(list).toHaveLength(1200);
    expect(list.at(-1)?.vehicle.model).toBe("Golf 1199");
    // Each page continues after the last vehicle of the previous one (keyset).
    expect(calls.pages).toEqual([
      { cursor: null, limit: 500 },
      { cursor: after(vehicles[499]!), limit: 500 },
      { cursor: after(vehicles[999]!), limit: 500 },
    ]);
    // Deterministic order: newest first, id breaks ties.
    expect(calls.orders.slice(0, 2)).toEqual(["created_at desc", "id asc"]);
  });

  it("needs a single request for a normal fleet", async () => {
    const { client, calls } = fakeClient({ vehicles: [vehicleRow(1), vehicleRow(2)] });
    expect(await new SupabaseDataProvider(client).listVehicles()).toHaveLength(2);
    expect(calls.pages).toHaveLength(1);
  });

  it("does not lose vehicles when the project's max-rows is below the page size", async () => {
    const vehicles = Array.from({ length: 300 }, (_, index) => vehicleRow(index));
    const { client, calls } = fakeClient({ vehicles, maxRows: 100 });
    const list = await new SupabaseDataProvider(client).listVehicles();
    expect(list.map((item) => item.vehicle.id)).toEqual(vehicles.map((row) => row.id));
    expect(calls.pages.map((page) => page.cursor)).toEqual([null, after(vehicles[99]!), after(vehicles[199]!)]);
  });

  it("stops at the page that holds all remaining vehicles (one full page needs one request)", async () => {
    const vehicles = Array.from({ length: 500 }, (_, index) => vehicleRow(index));
    const { client, calls } = fakeClient({ vehicles });
    expect(await new SupabaseDataProvider(client).listVehicles()).toHaveLength(500);
    expect(calls.pages).toHaveLength(1);
  });

  it("without a count, reads on until a page brings no new vehicle", async () => {
    const vehicles = Array.from({ length: 300 }, (_, index) => vehicleRow(index));
    const { client, calls } = fakeClient({ vehicles, maxRows: 100, answersCount: false });
    expect(await new SupabaseDataProvider(client).listVehicles()).toHaveLength(300);
    expect(calls.pages).toHaveLength(4);
  });

  it("stops when a server repeats the same page instead of reading forever", async () => {
    const vehicles = Array.from({ length: 50 }, (_, index) => vehicleRow(index));
    const { client, calls } = fakeClient({ vehicles, maxRows: 10, answersCount: false, ignoresCursor: true });
    expect(await new SupabaseDataProvider(client).listVehicles()).toHaveLength(10);
    expect(calls.pages).toHaveLength(2);
  });

  it("lists a vehicle only once when one is created while paging", async () => {
    const vehicles = Array.from({ length: 700 }, (_, index) => vehicleRow(index));
    const { client, state } = fakeClient({ vehicles });
    const provider = new SupabaseDataProvider(client);
    const original = client.from.bind(client);
    let requests = 0;
    vi.spyOn(client, "from").mockImplementation(((table: string) => {
      requests += 1;
      // Created by a colleague right after the first page was read (the newest – it would be first).
      if (requests === 2) state.vehicles = [{ ...vehicleRow(9999), created_at: "2026-10-28T11:00:00+00:00" }, ...state.vehicles];
      return original(table);
    }) as SupabaseClient["from"]);
    const list = await provider.listVehicles();
    const ids = list.map((item) => item.vehicle.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids).toEqual(vehicles.map((row) => row.id));
  });

  it("keeps every remaining vehicle when vehicles are deleted while paging", async () => {
    const vehicles = Array.from({ length: 600 }, (_, index) => vehicleRow(index));
    const { client, state } = fakeClient({ vehicles });
    const original = client.from.bind(client);
    let requests = 0;
    vi.spyOn(client, "from").mockImplementation(((table: string) => {
      requests += 1;
      // e.g. deleted in the dashboard right after the first page was read – offsets would now skip ten.
      if (requests === 2) state.vehicles = state.vehicles.slice(10);
      return original(table);
    }) as SupabaseClient["from"]);
    const list = await new SupabaseDataProvider(client).listVehicles();
    const listed = new Set(list.map((item) => item.vehicle.id));
    expect(state.vehicles.filter((row) => !listed.has(row.id))).toEqual([]);
    expect(list).toHaveLength(600);
  });
});

describe("SupabaseDataProvider.listVehicles with the real supabase-js client", () => {
  /** PostgREST-like: keyset `or` filter, limit, max-rows cap, count in Content-Range when asked for. */
  function postgrestClient(all: ListRow[], maxRows: number) {
    const state = { vehicles: all, beforeAnswer: (() => {}) as (request: number) => void };
    const requests: { cursor: string | null; limit: number; prefer: string | null }[] = [];
    const fetchImpl = async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(input instanceof Request ? input.url : String(input));
      const headers = new Headers(init.headers);
      const or = url.searchParams.get("or");
      const cursor = or === null ? null : or.replace(/^\((.*)\)$/, "$1");
      const limit = Number(url.searchParams.get("limit") ?? Infinity);
      requests.push({ cursor, limit, prefer: headers.get("prefer") });
      state.beforeAnswer(requests.length);
      const matching = afterCursor(state.vehicles, cursor);
      const rows = matching.slice(0, Math.min(limit, maxRows));
      const counted = /count=exact/.test(headers.get("prefer") ?? "");
      const total = counted ? String(matching.length) : "*";
      return new Response(JSON.stringify(rows), {
        status: 200,
        headers: {
          "content-type": "application/json",
          "content-range": rows.length > 0 ? `0-${rows.length - 1}/${total}` : `*/${total}`,
        },
      });
    };
    const client = createClient("https://project.supabase.co", "anon-key", {
      global: { fetch: fetchImpl as typeof fetch },
      auth: { persistSession: false, autoRefreshToken: false },
    });
    return { client, requests, state };
  }

  it("lists every vehicle when the project's max-rows (here 100) is below the page size", async () => {
    const vehicles = Array.from({ length: 300 }, (_, index) => vehicleRow(index));
    const { client, requests } = postgrestClient(vehicles, 100);
    const list = await new SupabaseDataProvider(client).listVehicles();
    expect(list).toHaveLength(300);
    expect(new Set(list.map((item) => item.vehicle.id)).size).toBe(300);
    expect(requests.map((request) => request.cursor)).toEqual([
      null,
      `created_at.lt."2026-10-28T10:00:00+00:00",and(created_at.eq."2026-10-28T10:00:00+00:00",id.gt.${vehicles[99]?.id})`,
      `created_at.lt."2026-10-28T10:00:00+00:00",and(created_at.eq."2026-10-28T10:00:00+00:00",id.gt.${vehicles[199]?.id})`,
    ]);
    expect(requests.every((request) => request.limit === 500 && request.prefer?.includes("count=exact"))).toBe(true);
  });

  it("needs a single request for a normal fleet", async () => {
    const { client, requests } = postgrestClient([vehicleRow(1), vehicleRow(2), vehicleRow(3)], 1000);
    expect(await new SupabaseDataProvider(client).listVehicles()).toHaveLength(3);
    expect(requests).toHaveLength(1);
  });

  it("keeps every remaining vehicle when vehicles are deleted while paging", async () => {
    const { client, requests, state } = postgrestClient(
      Array.from({ length: 300 }, (_, index) => vehicleRow(index)),
      100,
    );
    state.beforeAnswer = (request) => {
      // Ten of the first page deleted in the dashboard after it was answered.
      if (request === 2) state.vehicles = state.vehicles.slice(10);
    };
    const list = await new SupabaseDataProvider(client).listVehicles();
    const listed = new Set(list.map((item) => item.vehicle.id));
    expect(state.vehicles.filter((row) => !listed.has(row.id))).toEqual([]);
    expect(list).toHaveLength(300);
    expect(requests).toHaveLength(3);
  });
});

describe("SupabaseDataProvider before the hints migration (20261028000100) was applied", () => {
  const photoRow = (id: string) => ({
    id,
    vehicle_id: VEHICLE_ID,
    shot_key: "front",
    shot_order: 2,
    title: "Vorne",
    original_storage_path: `${VEHICLE_ID}/front/${id}.jpg`,
    processed_storage_path: `${VEHICLE_ID}/autoexperten_standard/front/${id}.jpg`,
    processed_preset: "autoexperten_standard",
    thumbnail_storage_path: null,
    width: 4000,
    height: 3000,
    taken_at: "2026-10-28T09:00:00+00:00",
    archived_at: null,
    created_at: "2026-10-28T09:00:00+00:00",
    updated_at: "2026-10-28T09:00:00+00:00",
  });

  /** PostgREST of a database without vehicle_photos.processing_warnings (or with it: `hasColumn`). */
  function postgrest({ hasColumn = false, otherMissingColumn = false } = {}) {
    const requests: { method: string; path: string; select: string | null; body: Record<string, unknown> | null }[] = [];
    const json = (status: number, body: unknown) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    const fetchImpl = async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(input instanceof Request ? input.url : String(input));
      const method = init.method ?? "GET";
      const select = url.searchParams.get("select");
      const body = typeof init.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : null;
      requests.push({ method, path: url.pathname, select, body });
      if (otherMissingColumn) {
        return json(400, { code: "42703", details: null, hint: null, message: "column vehicle_photos.focus does not exist" });
      }
      if (!hasColumn && select?.includes("processing_warnings")) {
        // PostgreSQL's answer for the unknown column (embedded: alias vehicle_photos_1).
        const table = url.pathname.endsWith("/vehicles") ? "vehicle_photos_1" : "vehicle_photos";
        return json(400, { code: "42703", details: null, hint: null, message: `column ${table}.processing_warnings does not exist` });
      }
      if (!hasColumn && body && "processing_warnings" in body) {
        return json(400, {
          code: "PGRST204",
          details: null,
          hint: null,
          message: "Could not find the 'processing_warnings' column of 'vehicle_photos' in the schema cache",
        });
      }
      if (method === "PATCH") return new Response(null, { status: 204 });
      if (url.pathname.endsWith("/vehicles")) {
        return new Response(JSON.stringify([{ ...vehicleRow(1), id: VEHICLE_ID, vehicle_photos: [photoRow("p1")] }]), {
          status: 200,
          headers: { "content-type": "application/json", "content-range": "0-0/1" },
        });
      }
      return json(200, [photoRow("p1"), photoRow("p2")]);
    };
    const client = createClient("https://project.supabase.co", "anon-key", {
      global: { fetch: fetchImpl as typeof fetch },
      auth: { persistSession: false, autoRefreshToken: false },
    });
    return { client, requests };
  }

  it("still lists vehicles and photos – without the hints – instead of failing every request", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { client, requests } = postgrest();
    const provider = new SupabaseDataProvider(client);

    const photos = await provider.listPhotos(VEHICLE_ID);
    expect(photos.map((photo) => [photo.id, photo.processingWarnings])).toEqual([
      ["p1", []],
      ["p2", []],
    ]);
    const list = await provider.listVehicles();
    expect(list.map((item) => [item.vehicle.id, item.photos.map((photo) => photo.id)])).toEqual([[VEHICLE_ID, ["p1"]]]);
    // Asked once with the column, then without it from now on.
    expect(requests.map((request) => request.select?.includes("processing_warnings"))).toEqual([true, false, false]);
    // Once, for the operator (supabase-js may warn about other things).
    const notes = warn.mock.calls.map((call) => String(call[0])).filter((text) => text.includes("processing_warnings"));
    expect(notes).toHaveLength(1);
    expect(notes[0]).toMatch(/20261028000100_photo_processing_warnings\.sql/);
    warn.mockRestore();
  });

  it("records a processed result without the hints", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { client, requests } = postgrest();
    const provider = new SupabaseDataProvider(client);
    const input = {
      photoId: "p1",
      preset: "autoexperten_standard" as const,
      processedStoragePath: `${VEHICLE_ID}/autoexperten_standard/front/p1.jpg`,
      warnings: [{ code: "mask_uncertain", message: "Die Freistellung ist unsicher – bitte das Ergebnis prüfen." }],
    };
    await provider.recordProcessedPhoto(input);
    await provider.recordProcessedPhoto(input);
    expect(requests.map((request) => [request.method, request.body])).toEqual([
      ["PATCH", { ...{ processed_storage_path: input.processedStoragePath, processed_preset: input.preset }, processing_warnings: input.warnings }],
      ["PATCH", { processed_storage_path: input.processedStoragePath, processed_preset: input.preset }],
      ["PATCH", { processed_storage_path: input.processedStoragePath, processed_preset: input.preset }],
    ]);
    warn.mockRestore();
  });

  it("reads and writes the hints once the column exists – and never hides another error", async () => {
    const { client, requests } = postgrest({ hasColumn: true });
    const provider = new SupabaseDataProvider(client);
    await provider.listPhotos(VEHICLE_ID);
    await provider.recordProcessedPhoto({
      photoId: "p1",
      preset: "autoexperten_standard",
      processedStoragePath: `${VEHICLE_ID}/autoexperten_standard/front/p1.jpg`,
      warnings: [],
    });
    expect(requests.map((request) => [request.method, request.select?.includes("processing_warnings") ?? null, request.body]))
      .toEqual([
        ["GET", true, null],
        [
          "PATCH",
          null,
          {
            processed_storage_path: `${VEHICLE_ID}/autoexperten_standard/front/p1.jpg`,
            processed_preset: "autoexperten_standard",
            processing_warnings: [],
          },
        ],
      ]);

    const broken = postgrest({ otherMissingColumn: true });
    await expect(new SupabaseDataProvider(broken.client).listPhotos(VEHICLE_ID)).rejects.toBeInstanceOf(AppError);
    expect(broken.requests).toHaveLength(1);
  });
});
