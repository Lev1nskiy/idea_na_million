// POST /api/data/import — загрузка дампа на бэкенд (кнопка «Загрузить»).
//
// Файл не разбираем на стороне сайта: правила переноса (проверка версии, маскирование,
// атомарность) живут в app/transfer.py. Сайт только передаёт файл и режим.
import { proxySiteRaw } from "@/server/backend";

export const dynamic = "force-dynamic";
export const maxDuration = 120;

export async function POST(req: Request) {
  const params = new URL(req.url).searchParams;
  const query = new URLSearchParams({
    mode: params.get("mode") ?? "merge",
    policy: params.get("policy") ?? "false",
    dry_run: params.get("dry_run") ?? "false",
  });
  const form = await req.formData();
  const file = form.get("file");
  if (!(file instanceof File)) {
    return Response.json({ ok: false, error: "Файл не приложен: выберите JSON-дамп." }, { status: 400 });
  }
  const res = await proxySiteRaw(`/data/import?${query}`, { method: "POST", body: form });
  if (!res) {
    return Response.json({ ok: false, error: "Бэкенд не подключён — импорт невозможен." }, { status: 503 });
  }
  return new Response(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}
