import { logEvent, nextId, store } from "@/server/store";

export const dynamic = "force-dynamic";

export async function POST(req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const body = (await req.json()) as { text?: string };
  if (!body.text?.trim()) return Response.json({ error: "text обязателен" }, { status: 400 });
  const st = store();
  const conv = st.conversations.find((c) => c.id === id);
  if (!conv) return Response.json({ error: "not found" }, { status: 404 });
  if (conv.status === "HOLD") {
    // ответ менеджера — автоматический takeover: уведомления гасятся, hold-loop встаёт
    conv.status = "HANDOFF";
    for (const n of st.notifications) if (n.convId === id) { n.active = false; n.read = true; }
    const ho = st.handoffs.find((h) => h.convId === id);
    if (ho) { ho.status = "taken"; ho.takenBy = "Менеджер А."; }
    st.messages.push({
      id: nextId("m"), convId: id, role: "system",
      text: "Менеджер подключился к диалогу. ИИ больше не отвечает клиенту.",
      at: new Date().toISOString(),
    });
    logEvent("takeover", "менеджер подключился ответом — hold-loop остановлен", { convId: id });
  }
  st.messages.push({
    id: nextId("m"), convId: id, role: "manager", text: body.text.trim(), at: new Date().toISOString(),
  });
  conv.updatedAt = new Date().toISOString();
  logEvent("manager_reply", "ответ менеджера → outbox клиента", { convId: id });
  return Response.json({ ok: true });
}
