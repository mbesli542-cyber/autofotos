/**
 * IdbDatabase against a minimal fake IndexedDB that behaves like browsers:
 * a request reports success first, the transaction commits (or aborts, e.g.
 * QuotaExceededError for large photo blobs) afterwards and rolls back.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { IdbDatabase } from "./idb";

type Row = Record<string, unknown>;

interface FakeRequest {
  result?: unknown;
  error: DOMException | null;
  onsuccess: (() => void) | null;
  onerror: (() => void) | null;
  onupgradeneeded?: (() => void) | null;
  onblocked?: (() => void) | null;
}

function createFakeIndexedDb() {
  const stores = new Map<string, Map<unknown, Row>>();
  const control = {
    /** Makes the next write transaction fail at commit (e.g. quota). */
    abortNextCommit: null as DOMException | null,
    /** Makes the next request fail: error event first (tx.error still null), then abort. */
    failNextRequest: null as DOMException | null,
    /** Mode of every transaction opened, in order. */
    transactions: [] as string[],
  };

  function request(): FakeRequest {
    return { error: null, onsuccess: null, onerror: null };
  }

  function transaction(_stores: unknown, mode: IDBTransactionMode = "readonly") {
    control.transactions.push(mode);
    const undo: (() => void)[] = [];
    let pending = 0;
    const abortWith = control.abortNextCommit;
    control.abortNextCommit = null;
    const tx = {
      error: null as DOMException | null,
      oncomplete: null as (() => void) | null,
      onerror: null as ((event: { target: unknown }) => void) | null,
      onabort: null as (() => void) | null,
      objectStore(name: string) {
        const rows = stores.get(name);
        if (!rows) throw new DOMException(`No store ${name}`, "NotFoundError");
        const run = (work: () => unknown) => {
          const req = request();
          const failWith = control.failNextRequest;
          control.failNextRequest = null;
          pending += 1;
          setTimeout(() => {
            if (failWith) {
              req.error = failWith;
              req.onerror?.();
              tx.onerror?.({ target: req }); // bubbles to the transaction
              tx.error = failWith;
              tx.onabort?.();
              return;
            }
            req.result = work();
            req.onsuccess?.();
            pending -= 1;
            if (pending === 0) setTimeout(commit, 0);
          }, 0);
          return req;
        };
        return {
          put: (value: Row) =>
            run(() => {
              const before = rows.get(value.id);
              rows.set(value.id, value);
              undo.push(() => (before ? rows.set(value.id, before) : rows.delete(value.id)));
              return value.id;
            }),
          delete: (key: unknown) =>
            run(() => {
              const before = rows.get(key);
              rows.delete(key);
              undo.push(() => before && rows.set(key, before));
            }),
          clear: () =>
            run(() => {
              const before = new Map(rows);
              rows.clear();
              undo.push(() => before.forEach((value, key) => rows.set(key, value)));
            }),
          get: (key: unknown) => run(() => rows.get(key)),
          getAll: () => run(() => [...rows.values()]),
        };
      },
    };
    function commit() {
      if (abortWith) {
        for (const step of undo.reverse()) step();
        tx.error = abortWith;
        tx.onabort?.();
        return;
      }
      tx.oncomplete?.();
    }
    return tx;
  }

  const factory = {
    open() {
      const req = request();
      setTimeout(() => {
        req.result = {
          objectStoreNames: { contains: (name: string) => stores.has(name) },
          createObjectStore: (name: string) => {
            stores.set(name, new Map());
            return { createIndex: () => undefined };
          },
          transaction,
        };
        req.onupgradeneeded?.();
        req.onsuccess?.();
      }, 0);
      return req;
    },
  };
  return { factory, control, stores };
}

function setup() {
  const fake = createFakeIndexedDb();
  vi.stubGlobal("indexedDB", fake.factory);
  const db = new IdbDatabase("test", 1, [{ name: "items", keyPath: "id" }]);
  return { db, ...fake };
}

const quotaExceeded = () => new DOMException("Quota exceeded at commit", "QuotaExceededError");

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("IdbDatabase writes resolve only when the transaction committed", () => {
  it("put and delete resolve after the commit", async () => {
    const { db } = setup();
    await db.put("items", { id: "a", value: 1 });
    expect(await db.get("items", "a")).toEqual({ id: "a", value: 1 });
    await db.delete("items", "a");
    expect(await db.get("items", "a")).toBeUndefined();
  });

  it("put rejects when the transaction aborts after the request succeeded", async () => {
    const { db, control, stores } = setup();
    await db.put("items", { id: "seed" });
    control.abortNextCommit = quotaExceeded();
    await expect(db.put("items", { id: "photo", blob: "large" })).rejects.toMatchObject({
      name: "QuotaExceededError",
    });
    expect(stores.get("items")?.has("photo")).toBe(false);
  });

  it("put rejects with the cause when a request fails", async () => {
    const { db, control } = setup();
    control.failNextRequest = new DOMException("Key already exists", "ConstraintError");
    await expect(db.put("items", { id: "a" })).rejects.toMatchObject({ name: "ConstraintError" });
  });

  it("delete rejects when the transaction aborts", async () => {
    const { db, control } = setup();
    await db.put("items", { id: "a" });
    control.abortNextCommit = quotaExceeded();
    await expect(db.delete("items", "a")).rejects.toMatchObject({ name: "QuotaExceededError" });
    expect(await db.get("items", "a")).toEqual({ id: "a" });
  });

  it("clear rejects when the transaction aborts (instead of never settling)", async () => {
    const { db, control } = setup();
    await db.put("items", { id: "a" });
    control.abortNextCommit = new DOMException("Aborted", "AbortError");
    const outcome = await Promise.race([
      db.clear(["items"]).then(
        () => "resolved",
        (error: DOMException) => error.name,
      ),
      new Promise((resolve) => setTimeout(() => resolve("pending"), 200)),
    ]);
    expect(outcome).toBe("AbortError");
    expect(await db.getAll("items")).toEqual([{ id: "a" }]);
  });
});

describe("IdbDatabase.update (read-modify-write of one record)", () => {
  type Counter = { id: string; n: number; kept: string };

  it("reads and writes the record in one readwrite transaction", async () => {
    const { db, control } = setup();
    await db.put<Counter>("items", { id: "a", n: 1, kept: "x" });
    control.transactions.length = 0;

    const stored = await db.update<Counter>("items", "a", (current) => ({ ...current, n: current.n + 1 }));
    expect(stored).toEqual({ id: "a", n: 2, kept: "x" });
    // No second transaction: nothing another one writes can land between the read and the write.
    expect(control.transactions).toEqual(["readwrite"]);
    expect(await db.get("items", "a")).toEqual({ id: "a", n: 2, kept: "x" });
  });

  it("writes nothing when there is no such record", async () => {
    const { db, stores } = setup();
    await db.put("items", { id: "seed" });
    const change = vi.fn((current: Counter) => current);
    expect(await db.update<Counter>("items", "missing", change)).toBeUndefined();
    expect(change).not.toHaveBeenCalled();
    expect([...(stores.get("items")?.keys() ?? [])]).toEqual(["seed"]);
  });

  it("rejects and keeps the record when the transaction aborts", async () => {
    const { db, control } = setup();
    await db.put<Counter>("items", { id: "a", n: 1, kept: "x" });
    control.abortNextCommit = quotaExceeded();
    await expect(db.update<Counter>("items", "a", (current) => ({ ...current, n: 2 }))).rejects.toMatchObject({
      name: "QuotaExceededError",
    });
    expect(await db.get("items", "a")).toEqual({ id: "a", n: 1, kept: "x" });
  });

  it("works the same without IndexedDB (in-memory fallback)", async () => {
    // jsdom has no IndexedDB: this database keeps its data in memory.
    const db = new IdbDatabase("memory", 1, [{ name: "items", keyPath: "id" }]);
    await db.put<Counter>("items", { id: "a", n: 1, kept: "x" });
    expect(await db.update<Counter>("items", "a", (current) => ({ ...current, n: 2 }))).toEqual({ id: "a", n: 2, kept: "x" });
    expect(await db.get("items", "a")).toEqual({ id: "a", n: 2, kept: "x" });
    expect(await db.update<Counter>("items", "b", (current) => current)).toBeUndefined();
    expect(await db.getAll("items")).toEqual([{ id: "a", n: 2, kept: "x" }]);
  });
});
