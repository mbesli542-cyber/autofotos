/**
 * Minimal promise wrapper around IndexedDB (no dependency).
 *
 * Falls back to an in-memory store when IndexedDB is unavailable (e.g. some
 * private browsing modes) so the app keeps working – data then only lives
 * for the current page session.
 */

export interface IdbStoreDefinition {
  name: string;
  keyPath: string;
  indexes?: readonly { name: string; keyPath: string }[];
}

type MemoryStores = Map<string, Map<IDBValidKey, unknown>>;

/** Records of one store to delete (deleteMany). */
export interface IdbDeletion {
  store: string;
  /** Records with these keys. */
  keys?: readonly IDBValidKey[];
  /** Records whose index `name` has the value `value`. */
  index?: { name: string; value: IDBValidKey };
  /** Records whose (string) key starts with one of these prefixes. */
  keyPrefixes?: readonly string[];
}

/** Upper bound of the string keys starting with a prefix. */
const PREFIX_END = "\uffff";

export function isIndexedDbAvailable(): boolean {
  return typeof indexedDB !== "undefined";
}

function requestToPromise<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

/**
 * Settles when a write transaction is durable: request success is not a
 * commit – the transaction can still abort afterwards (e.g. QuotaExceededError
 * for large photo blobs), which rolls the write back.
 */
function transactionToPromise(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    // A failed request aborts the transaction; tx.error then holds the cause.
    tx.onabort = () => reject(tx.error ?? new DOMException("IndexedDB transaction aborted", "AbortError"));
    tx.onerror = () => {
      if (tx.error) reject(tx.error);
    };
  });
}

export class IdbDatabase {
  private dbPromise: Promise<IDBDatabase | null> | null = null;
  private memory: MemoryStores | null = null;

  constructor(
    private readonly name: string,
    private readonly version: number,
    private readonly stores: readonly IdbStoreDefinition[],
  ) {}

  private open(): Promise<IDBDatabase | null> {
    if (this.dbPromise) return this.dbPromise;
    this.dbPromise = new Promise<IDBDatabase | null>((resolve) => {
      if (!isIndexedDbAvailable()) {
        resolve(null);
        return;
      }
      try {
        const request = indexedDB.open(this.name, this.version);
        request.onupgradeneeded = () => {
          const db = request.result;
          for (const store of this.stores) {
            if (db.objectStoreNames.contains(store.name)) continue;
            const objectStore = db.createObjectStore(store.name, { keyPath: store.keyPath });
            for (const index of store.indexes ?? []) {
              objectStore.createIndex(index.name, index.keyPath);
            }
          }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => resolve(null);
        request.onblocked = () => resolve(null);
      } catch {
        resolve(null);
      }
    });
    return this.dbPromise;
  }

  private memoryStore(storeName: string): Map<IDBValidKey, unknown> {
    this.memory ??= new Map(this.stores.map((store) => [store.name, new Map()]));
    let store = this.memory.get(storeName);
    if (!store) {
      store = new Map();
      this.memory.set(storeName, store);
    }
    return store;
  }

  private keyOf(storeName: string, value: unknown): IDBValidKey {
    const keyPath = this.stores.find((store) => store.name === storeName)?.keyPath ?? "id";
    return (value as Record<string, IDBValidKey>)[keyPath] as IDBValidKey;
  }

  async get<T>(storeName: string, key: IDBValidKey): Promise<T | undefined> {
    const db = await this.open();
    if (!db) return this.memoryStore(storeName).get(key) as T | undefined;
    const tx = db.transaction(storeName, "readonly");
    return (await requestToPromise(tx.objectStore(storeName).get(key))) as T | undefined;
  }

  async getAll<T>(storeName: string): Promise<T[]> {
    const db = await this.open();
    if (!db) return [...this.memoryStore(storeName).values()] as T[];
    const tx = db.transaction(storeName, "readonly");
    return (await requestToPromise(tx.objectStore(storeName).getAll())) as T[];
  }

  async getAllByIndex<T>(
    storeName: string,
    indexName: string,
    value: IDBValidKey,
  ): Promise<T[]> {
    const db = await this.open();
    if (!db) {
      const keyPath =
        this.stores.find((s) => s.name === storeName)?.indexes?.find((i) => i.name === indexName)
          ?.keyPath ?? indexName;
      return [...this.memoryStore(storeName).values()].filter(
        (item) => (item as Record<string, unknown>)[keyPath] === value,
      ) as T[];
    }
    const tx = db.transaction(storeName, "readonly");
    const index = tx.objectStore(storeName).index(indexName);
    return (await requestToPromise(index.getAll(value))) as T[];
  }

  async put<T>(storeName: string, value: T): Promise<void> {
    const db = await this.open();
    if (!db) {
      this.memoryStore(storeName).set(this.keyOf(storeName, value), value);
      return;
    }
    const tx = db.transaction(storeName, "readwrite");
    tx.objectStore(storeName).put(value);
    await transactionToPromise(tx);
  }

  /**
   * Read-modify-write of one record in ONE readwrite transaction, so a write
   * of another transaction can never land between the read and the write
   * (and be overwritten with the older copy). `change` gets the stored record
   * – it is not called when there is none – and returns the record to store;
   * it must not throw. Resolves with the stored record (undefined: none) once
   * the transaction committed.
   */
  async update<T>(storeName: string, key: IDBValidKey, change: (current: T) => T): Promise<T | undefined> {
    const db = await this.open();
    if (!db) {
      const store = this.memoryStore(storeName);
      const current = store.get(key) as T | undefined;
      if (current === undefined) return undefined;
      const next = change(current);
      store.set(key, next);
      return next;
    }
    const tx = db.transaction(storeName, "readwrite");
    const store = tx.objectStore(storeName);
    const committed = transactionToPromise(tx);
    let next: T | undefined;
    const request = store.get(key);
    // Still inside the transaction: the write is queued before it can commit.
    request.onsuccess = () => {
      const current = request.result as T | undefined;
      if (current === undefined) return;
      next = change(current);
      store.put(next);
    };
    await committed;
    return next;
  }

  async putMany<T>(storeName: string, values: readonly T[]): Promise<void> {
    const db = await this.open();
    if (!db) {
      for (const value of values) {
        this.memoryStore(storeName).set(this.keyOf(storeName, value), value);
      }
      return;
    }
    const tx = db.transaction(storeName, "readwrite");
    const store = tx.objectStore(storeName);
    for (const value of values) store.put(value);
    await transactionToPromise(tx);
  }

  async delete(storeName: string, key: IDBValidKey): Promise<void> {
    const db = await this.open();
    if (!db) {
      this.memoryStore(storeName).delete(key);
      return;
    }
    const tx = db.transaction(storeName, "readwrite");
    tx.objectStore(storeName).delete(key);
    await transactionToPromise(tx);
  }

  /**
   * Deletes records of several stores in ONE readwrite transaction (all or
   * nothing): by key, by index value and by key prefix – without reading the
   * records themselves (e.g. large photo blobs).
   */
  async deleteMany(deletions: readonly IdbDeletion[]): Promise<void> {
    const db = await this.open();
    if (!db) {
      for (const deletion of deletions) this.deleteFromMemory(deletion);
      return;
    }
    const storeNames = [...new Set(deletions.map((deletion) => deletion.store))];
    if (storeNames.length === 0) return;
    const tx = db.transaction(storeNames, "readwrite");
    const committed = transactionToPromise(tx);
    for (const { store: name, keys, index, keyPrefixes } of deletions) {
      const store = tx.objectStore(name);
      for (const key of keys ?? []) store.delete(key);
      for (const prefix of keyPrefixes ?? []) store.delete(IDBKeyRange.bound(prefix, `${prefix}${PREFIX_END}`));
      if (index) {
        const request = store.index(index.name).getAllKeys(index.value);
        // Still inside the transaction: the deletes are queued before it can commit.
        request.onsuccess = () => {
          for (const key of request.result) store.delete(key);
        };
      }
    }
    await committed;
  }

  private deleteFromMemory({ store: name, keys, index, keyPrefixes }: IdbDeletion): void {
    const store = this.memoryStore(name);
    for (const key of keys ?? []) store.delete(key);
    const keyPath = index
      ? (this.stores.find((s) => s.name === name)?.indexes?.find((i) => i.name === index.name)?.keyPath ?? index.name)
      : null;
    for (const [key, value] of [...store]) {
      const byPrefix = typeof key === "string" && (keyPrefixes ?? []).some((prefix) => key.startsWith(prefix));
      const byIndex = index && keyPath !== null && (value as Record<string, unknown>)[keyPath] === index.value;
      if (byPrefix || byIndex) store.delete(key);
    }
  }

  async clear(storeNames: readonly string[]): Promise<void> {
    const db = await this.open();
    if (!db) {
      for (const name of storeNames) this.memoryStore(name).clear();
      return;
    }
    const tx = db.transaction([...storeNames], "readwrite");
    for (const name of storeNames) tx.objectStore(name).clear();
    await transactionToPromise(tx);
  }
}
