import { backendConfigured, proxySite } from "@/server/backend";
import { db } from "@/db";
import { sql } from "drizzle-orm";

export const dynamic = "force-dynamic";

/**
 * Healthcheck: демо-движок работает на in-memory сторе и от Postgres не зависит,
 * поэтому при недоступной БД сервис остаётся ok (режим degraded — только для локального запуска без СУБД).
 */
export async function GET() {
  // Бэкенд — источник истины: если он подключён и жив, отдаём его состояние.
  const proxied = await proxySite("/health");
  if (proxied) {
    const data = (await proxied.json()) as Record<string, unknown>;
    return Response.json({ ...data, service: "los-ai-crm", engine: "backend" });
  }

  if (!db) {
    return Response.json({
      ok: true,
      service: "los-ai-crm",
      db: "not_configured",
      engine: backendConfigured ? "mock-fallback" : "mock",
      note: backendConfigured
        ? "бэкенд недоступен, Postgres не задан — отвечает демо-движок сайта"
        : "Postgres не задан — демо-движок работает без него",
      time: new Date().toISOString(),
    });
  }

  try {
    await db.execute(sql`select 1`);
    return Response.json({
      ok: true,
      service: "los-ai-crm",
      db: "up",
      engine: backendConfigured ? "mock-fallback" : "mock",
      note: backendConfigured ? "бэкенд недоступен, работает демо-движок сайта" : undefined,
      time: new Date().toISOString(),
    });
  } catch {
    return Response.json({
      ok: true,
      service: "los-ai-crm",
      db: "down",
      degraded: true,
      engine: backendConfigured ? "mock-fallback" : "mock",
      note: "демо-движок автономен; Postgres нужен только для персистентности",
      time: new Date().toISOString(),
    });
  }
}
