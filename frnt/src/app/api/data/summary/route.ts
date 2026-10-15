// GET /api/data/summary — что лежит в БД: для отчёта на странице «Данные».
import { proxySiteRaw } from "@/server/backend";

export const dynamic = "force-dynamic";

export async function GET() {
  const res = await proxySiteRaw("/data/summary");
  if (!res) {
    return Response.json(
      { ok: false, error: "Бэкенд не подключён: перенос данных работает только с CRM-бэкендом." },
      { status: 503 },
    );
  }
  return new Response(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}
