import { logEvent, nextId, store } from "@/server/store";

export const dynamic = "force-dynamic";

/** Менеджер подключается (takeover) — дублирование уведомлений прекращается (п. 0 №17). */
export async function POST(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const st = store();
  const conv = st.conversations.find((c) => c.id === id);
  if (!conv) return Response.json({ error: "not found" }, { status: 404 });
  conv.status = "HANDOFF";
  conv.updatedAt = new Date().toISOString();
  for (const n of st.notifications) if (n.convId === id) { n.active = false; n.read = true; }
  const ho = st.handoffs.find((h) => h.convId === id);
  if (ho) { ho.status = "taken"; ho.takenBy = "Менеджер А."; }
  st.messages.push({
    id: nextId("m"), convId: id, role: "system",
    text: "Менеджер подключился к диалогу. ИИ больше не отвечает клиенту (кроме технических сообщений).",
    at: new Date().toISOString(),
  });
  logEvent("takeover", "менеджер подключился — hold-loop остановлен", { convId: id });
  return Response.json({ ok: true, status: conv.status });
}
