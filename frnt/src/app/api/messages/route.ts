import { proxySite } from "@/server/backend";
import { processIncoming } from "@/server/engine";
import { tick } from "@/server/store";
import type { Channel } from "@/lib/types";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const raw = await req.text();
  const body = JSON.parse(raw || "{}") as { client_id?: string; channel?: Channel; text?: string };
  if (!body.client_id || !body.text) {
    return Response.json({ error: "client_id и text обязательны" }, { status: 400 });
  }

  // Реальный пайплайн — на бэкенде (policy, tools, guardrails, БД), mock остаётся фоллбеком.
  const proxied = await proxySite("/messages", { method: "POST", body: raw });
  if (proxied) return proxied;

  tick();
  const result = await processIncoming(body.client_id, body.channel ?? "web", body.text);
  return Response.json(result);
}
