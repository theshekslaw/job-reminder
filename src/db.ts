/**
 * Postgres client. Bun auto-loads .env, so DATABASE_URL comes from there.
 * The DB is the ONLY system of record (DESIGN.md) — fail fast when it's down.
 */

import postgres from "postgres";

const url = process.env.DATABASE_URL ?? "postgres://jobhunt:jobhunt@localhost:5436/jobhunt";

export const sql = postgres(url, {
  max: 5,
  onnotice: () => {}, // silence NOTICEs (e.g. IF NOT EXISTS)
  connect_timeout: 5,
});

export async function assertDb(): Promise<void> {
  try {
    await sql`SELECT 1`;
  } catch {
    console.error("db: cannot reach Postgres — run `make db-up` first");
    process.exit(1);
  }
}
