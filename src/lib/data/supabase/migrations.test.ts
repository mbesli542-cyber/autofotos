/**
 * Static guard for the SQL migrations (supabase/migrations): checks the
 * definitions in effect after all migrations (in order), so a later migration
 * cannot silently bring back the slow storage check or the old
 * add_vehicle_photo(), open the data to other roles (policies, grants,
 * SECURITY DEFINER), remove the original protection, or drop a column the
 * app reads. Their behaviour was tested against PostgreSQL 16 (throw-away
 * cluster, roles and RLS as in Supabase).
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { PHOTO_COLUMNS, VEHICLE_COLUMNS } from "./mappers";

const MIGRATIONS_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "../../../../supabase/migrations");

/** SQL without `--` comments (the headers describe what they must not do). */
function withoutComments(sql: string): string {
  return sql.replace(/--.*$/gm, "");
}

/** All migrations in the order they are applied – without comments. */
function migrations(): { file: string; sql: string }[] {
  return readdirSync(MIGRATIONS_DIR)
    .filter((file) => /^\d{14}_[a-z0-9_]+\.sql$/.test(file))
    .sort()
    .map((file) => ({ file, sql: withoutComments(readFileSync(join(MIGRATIONS_DIR, file), "utf8")) }));
}

/** Statements of a kind (`create policy …;`) across all migrations, in order. */
function statements(pattern: RegExp): { file: string; text: string }[] {
  return migrations().flatMap(({ file, sql }) => [...sql.matchAll(pattern)].map((match) => ({ file, text: match[0] })));
}

interface FunctionDefinition {
  file: string;
  /** Everything between the name and the body: arguments, returns, language, security. */
  header: string;
  body: string;
}

/**
 * The definition of public.<name>() in effect after all migrations – the
 * last `create [or replace] function` (any dollar quoting), null once dropped.
 */
function functionInEffect(name: string): FunctionDefinition | null {
  let current: FunctionDefinition | null = null;
  for (const { file, sql } of migrations()) {
    const created = sql.match(new RegExp(String.raw`create\s+(?:or\s+replace\s+)?function\s+public\.${name}\s*\(`, "gi"));
    const definitions = [
      ...sql.matchAll(
        new RegExp(
          String.raw`create\s+(?:or\s+replace\s+)?function\s+public\.${name}\s*\(([\s\S]*?)\bas\s+\$(\w*)\$([\s\S]*?)\$\2\$`,
          "gi",
        ),
      ),
    ];
    // A definition this guard cannot read (e.g. a body in single quotes) must not slip through.
    expect(definitions.length, `${file}: readable definitions of ${name}()`).toBe(created?.length ?? 0);
    const drops = [...sql.matchAll(new RegExp(String.raw`drop\s+function\s+(?:if\s+exists\s+)?public\.${name}\b`, "gi"))];
    const events = [
      ...definitions.map((match) => ({
        index: match.index ?? 0,
        definition: { file, header: match[1] ?? "", body: match[3] ?? "" } as FunctionDefinition | null,
      })),
      ...drops.map((match) => ({ index: match.index ?? 0, definition: null })),
    ].sort((a, b) => a.index - b.index);
    for (const event of events) current = event.definition;
  }
  return current;
}

/** Columns of public.<table> after all migrations (create table, add/drop/rename column, drop table). */
function tableColumns(table: string): Set<string> {
  const columns = new Set<string>();
  for (const { sql } of migrations()) {
    const events: { index: number; apply: () => void }[] = [];
    for (const match of sql.matchAll(new RegExp(String.raw`create\s+table\s+(?:if\s+not\s+exists\s+)?public\.${table}\s*\(([\s\S]*?)\n\);`, "gi"))) {
      events.push({
        index: match.index ?? 0,
        apply: () => {
          for (const line of match[1]?.split("\n") ?? []) {
            const column = /^\s{2}([a-z_]+)\s+[a-z]/.exec(line)?.[1];
            if (column) columns.add(column);
          }
        },
      });
    }
    for (const match of sql.matchAll(new RegExp(String.raw`drop\s+table\s+(?:if\s+exists\s+)?public\.${table}\b`, "gi"))) {
      events.push({ index: match.index ?? 0, apply: () => columns.clear() });
    }
    for (const match of sql.matchAll(new RegExp(String.raw`alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?public\.${table}\s+([^;]*);`, "gi"))) {
      const actions = match[1] ?? "";
      events.push({
        index: match.index ?? 0,
        apply: () => {
          for (const add of actions.matchAll(/\badd\s+column\s+(?:if\s+not\s+exists\s+)?([a-z_]+)/gi)) columns.add(add[1] ?? "");
          for (const drop of actions.matchAll(/\bdrop\s+column\s+(?:if\s+exists\s+)?([a-z_]+)/gi)) columns.delete(drop[1] ?? "");
          for (const rename of actions.matchAll(/\brename\s+column\s+([a-z_]+)\s+to\s+([a-z_]+)/gi)) {
            columns.delete(rename[1] ?? "");
            columns.add(rename[2] ?? "");
          }
        },
      });
    }
    for (const event of events.sort((a, b) => a.index - b.index)) event.apply();
  }
  return columns;
}

describe("supabase migrations", () => {
  it("are named so that they apply in order", () => {
    const files = readdirSync(MIGRATIONS_DIR).filter((file) => file.endsWith(".sql"));
    expect(files.length).toBeGreaterThanOrEqual(3);
    for (const file of files) expect(file).toMatch(/^\d{14}_[a-z0-9_]+\.sql$/);
    expect(migrations().map(({ file }) => file).slice(0, 3)).toEqual([
      "20261007000000_initial_schema.sql",
      "20261028000000_storage_access_index_and_late_photo_uploads.sql",
      "20261028000100_photo_processing_warnings.sql",
    ]);
  });

  it("storage policies look up the vehicle by primary key", () => {
    const definition = functionInEffect("can_access_vehicle_object");
    const body = definition?.body ?? "";
    // SECURITY INVOKER: the vehicles' RLS decides which objects a user may access.
    expect(definition?.header).toMatch(/\bsecurity\s+invoker\b/i);
    // v.id::text = … cannot use the index: every object scanned all vehicles.
    expect(body).not.toMatch(/v\.id::text/);
    const guard = body.match(
      /v\.id = case\s+when split_part\(object_name, '\/', 1\) ~ '([^']+)'\s+then split_part\(object_name, '\/', 1\)::uuid\s+end/,
    );
    expect(guard).not.toBeNull();
    // Only the canonical lowercase form is cast – the only one v.id::text
    // ever matched – anything else yields false instead of a cast error.
    const castable = new RegExp(guard?.[1] ?? "(?!)");
    expect(castable.test("5d0c6f1e-63a1-4c84-9a5f-0d5b3f2c6301")).toBe(true);
    for (const segment of [
      "5D0C6F1E-63A1-4C84-9A5F-0D5B3F2C6301",
      "{5d0c6f1e-63a1-4c84-9a5f-0d5b3f2c6301}",
      "5d0c6f1e63a14c849a5f0d5b3f2c6301",
      "5d0c6f1e-63a1-4c84-9a5f-0d5b3f2c6301 ",
      "",
      "fahrzeug",
    ]) {
      expect(castable.test(segment)).toBe(false);
    }
  });

  it("add_vehicle_photo keeps the newer photo when an older capture arrives late", () => {
    const definition = functionInEffect("add_vehicle_photo");
    const body = definition?.body ?? "";
    // SECURITY INVOKER: RLS applies to every photo it reads and writes.
    expect(definition?.header).toMatch(/\bsecurity\s+invoker\b/i);
    // Calls for one shot wait for each other …
    expect(body).toMatch(/pg_advisory_xact_lock\(/);
    // … an older capture is inserted as archived instead of replacing …
    expect(body).toMatch(/if found and p_taken_at < active_taken_at then/);
    expect(body).toMatch(/taken_at, archived_at\s*\)\s*values/);
    // … and a device clock running ahead cannot pin the active photo (the
    // same rule as the app: src/lib/photos/capture-order.ts).
    expect(body).toMatch(/least\(p\.taken_at, p\.created_at \+ interval '5 minutes'\) into active_taken_at/);
    // A new photo starts without the processor's hints of the photo it replaces.
    expect(body).not.toMatch(/processing_warnings/);
  });

  it("originals stay immutable – the processed fields and their hints may change", () => {
    const body = functionInEffect("protect_vehicle_photo_original")?.body ?? "";
    expect(body).toMatch(/new\.original_storage_path is distinct from old\.original_storage_path/);
    expect(body).toMatch(/new\.vehicle_id is distinct from old\.vehicle_id/);
    expect(body).toMatch(/new\.shot_key is distinct from old\.shot_key/);
    expect(body).toMatch(/raise exception/);
    expect(body).not.toMatch(/processed_storage_path|processed_preset|processing_warnings/);
    // … and its trigger stays in place: the last statement about it creates it, nothing disables it.
    const trigger = statements(/(create|drop)\s+trigger\s+(?:if\s+exists\s+)?vehicle_photos_protect_original\b[^;]*;/gi);
    expect(trigger.at(-1)?.text).toMatch(
      /^create\s+trigger\s+vehicle_photos_protect_original\s+before\s+update\s+on\s+public\.vehicle_photos\s+for\s+each\s+row\s+execute\s+function\s+public\.protect_vehicle_photo_original\(\)/i,
    );
    expect(statements(/alter\s+table[^;]*\bdisable\s+trigger\b[^;]*;/gi)).toEqual([]);
  });

  it("stores the processor's hints as a bounded array next to the processed version", () => {
    const sql = migrations().find(({ file }) => file === "20261028000100_photo_processing_warnings.sql")?.sql ?? "";
    expect(sql).toMatch(/alter table public\.vehicle_photos\s+add column if not exists processing_warnings jsonb;/);
    // Re-runnable, and jsonb_array_length only ever sees an array.
    expect(sql).toMatch(/drop constraint if exists vehicle_photos_processing_warnings_check;/);
    expect(sql).toMatch(
      /processing_warnings is null\s+or case\s+when jsonb_typeof\(processing_warnings\) = 'array'\s+then jsonb_array_length\(processing_warnings\) <= 20\s+and octet_length\(processing_warnings::text\) <= 16384/,
    );
    // Access stays as it is: no policy, grant or RLS change.
    expect(sql).not.toMatch(/\b(policy|grant|revoke|row level security)\b/i);
  });

  it("never weakens the access rules of the vehicle tables after the initial schema", () => {
    for (const { file, sql } of migrations().slice(1)) {
      expect(sql, file).not.toMatch(/disable row level security/i);
      expect(sql, file).not.toMatch(/drop policy/i);
    }
  });

  it("lets only signed-in members in: every policy is for `authenticated`, none allows everything", () => {
    const policies = statements(/\b(?:create|alter)\s+policy\b[^;]*;/gi);
    expect(policies.length).toBeGreaterThan(0);
    for (const { file, text } of policies) {
      // Without a `to` clause a policy applies to every role, anon included.
      const head = text.split(/\b(?:using|with\s+check)\b/i)[0] ?? "";
      const roles = /\bto\s+([a-z_][\w\s,]*?)\s*(?:;|$)/i.exec(head)?.[1]?.split(/\s*,\s*/) ?? [];
      if (/^create/i.test(text) || roles.length > 0) expect(roles, `${file}: ${text}`).toEqual(["authenticated"]);
      expect(text, file).not.toMatch(/\b(?:using|with\s+check)\s*\(\s*true\s*\)/i);
    }
    for (const { file, text } of statements(/\bgrant\b[^;]*;/gi)) {
      expect(text, file).not.toMatch(/\bto\s+[^;]*\b(?:anon|public)\b/i);
    }
  });

  it("keeps SECURITY DEFINER to the membership helpers (it bypasses RLS)", () => {
    const definers = statements(/\bcreate\s+(?:or\s+replace\s+)?function\s+public\.(\w+)\s*\([\s\S]*?\bas\s+\$/gi)
      .filter(({ text }) => /\bsecurity\s+definer\b/i.test(text))
      .map(({ text }) => /function\s+public\.(\w+)/i.exec(text)?.[1]);
    expect(new Set(definers)).toEqual(new Set(["is_organization_member", "current_organization_id", "handle_new_user"]));
    expect(statements(/\balter\s+function\b[^;]*\bsecurity\s+definer\b[^;]*;/gi)).toEqual([]);
  });

  it("creates every column the app reads – and no later migration drops one", () => {
    const photoColumns = tableColumns("vehicle_photos");
    for (const column of PHOTO_COLUMNS.split(", ")) expect(photoColumns, column).toContain(column);
    const vehicleColumns = tableColumns("vehicles");
    for (const column of VEHICLE_COLUMNS.split(", ")) expect(vehicleColumns, column).toContain(column);
  });
});
