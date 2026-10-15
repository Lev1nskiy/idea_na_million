import { db } from "@/db";
import { sql } from "drizzle-orm";

export const dynamic = "force-dynamic";

/**
 * Healthcheck: демо-движок работает на in-memory сторе и от Postgres не зависит,
 * поэтому при недоступной БД сервис остаётся ok (режим degraded — только для локального запуска без СУБД).
 */
export async function GET() {
  try {
    await db.execute(sql`select 1`);
    return Response.json({ ok: true, service: "synapse-ai-crm", db: "up", time: new Date().toISOString() });
  } catch {
    return Response.json({
      ok: true,
      service: "synapse-ai-crm",
      db: "down",
      degraded: true,
      note: "демо-движок автономен; Postgres нужен только для персистентности",
      time: new Date().toISOString(),
    });
  }
}
