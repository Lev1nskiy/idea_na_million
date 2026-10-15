// GET /api/data/export?mask=true — выгрузка состояния БД одним JSON (кнопка «Скачать дамп»).
import { proxySiteRaw } from "@/server/backend";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const mask = new URL(req.url).searchParams.get("mask") ?? "true";
  const res = await proxySiteRaw(`/data/export?mask=${encodeURIComponent(mask)}`);
  if (!res) {
    return Response.json({ ok: false, error: "Бэкенд не подключён — дамп недоступен." }, { status: 503 });
  }
  const headers = new Headers({ "Content-Type": "application/json; charset=utf-8" });
  // Имя файла приходит от бэкенда (crm-state-ГГГГ-ММ-ДД.json) — сохраняем его при скачивании.
  const disposition = res.headers.get("content-disposition");
  if (disposition) headers.set("Content-Disposition", disposition);
  return new Response(await res.arrayBuffer(), { status: res.status, headers });
}
