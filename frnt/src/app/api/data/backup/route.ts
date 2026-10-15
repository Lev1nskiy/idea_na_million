// GET /api/data/backup — бинарный снимок SQLite (кнопка «Скачать БД»).
import { proxySiteRaw } from "@/server/backend";

export const dynamic = "force-dynamic";

export async function GET() {
  const res = await proxySiteRaw("/data/backup");
  if (!res) {
    return Response.json({ ok: false, error: "Бэкенд не подключён — снимок БД недоступен." }, { status: 503 });
  }
  const headers = new Headers({ "Content-Type": "application/octet-stream" });
  const disposition = res.headers.get("content-disposition");
  if (disposition) headers.set("Content-Disposition", disposition);
  return new Response(await res.arrayBuffer(), { status: res.status, headers });
}
