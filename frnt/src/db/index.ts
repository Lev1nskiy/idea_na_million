import { drizzle, type NodePgDatabase } from "drizzle-orm/node-postgres";
import { Pool } from "pg";

/**
 * Postgres — опциональный слой персистентности: демо-движок и мост к backend работают без него.
 * Поэтому модуль не бросает исключение при пустом DATABASE_URL, а отдаёт `db = null`
 * (см. /api/health: `db: "not_configured"`).
 */
const databaseUrl = process.env.DATABASE_URL;

const globalForDb = globalThis as typeof globalThis & {
  __arenaNextJsPostgresqlPool?: Pool;
};

export const pool = databaseUrl
  ? globalForDb.__arenaNextJsPostgresqlPool ?? new Pool({ connectionString: databaseUrl })
  : null;

if (pool && process.env.NODE_ENV !== "production") {
  globalForDb.__arenaNextJsPostgresqlPool = pool;
}

export const db: NodePgDatabase | null = pool ? drizzle(pool) : null;
