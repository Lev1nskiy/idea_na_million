import { proxySite } from "@/server/backend";
import { logEvent, nextId, store } from "@/server/store";

export const dynamic = "force-dynamic";

export async function POST(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const proxied = await proxySite(`/conversations/${id}/resolve`, { method: "POST", body: "{}" });
  if (proxied) return proxied;

  const st = store();
  const conv = st.conversations.find((c) => c.id === id);
  if (!conv) return Response.json({ error: "not found" }, { status: 404 });
  conv.status = "RESOLVED";
  conv.updatedAt = new Date().toISOString();
  for (const n of st.notifications) if (n.convId === id) n.active = false;
  const ho = st.handoffs.find((h) => h.convId === id);
  if (ho) ho.status = "resolved";
  st.messages.push({
    id: nextId("m"), convId: id, role: "system", text: "Диалог закрыт. Настроение клиента стабилизировано.", at: new Date().toISOString(),
  });
  logEvent("resolve", "диалог закрыт (RESOLVED)", { convId: id });
  return Response.json({ ok: true });
}
