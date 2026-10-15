import { proxySite } from "@/server/backend";
import { convMessages, store, tick } from "@/server/store";

export const dynamic = "force-dynamic";

export async function GET(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const proxied = await proxySite(`/conversations/${id}`);
  if (proxied) return proxied;

  tick();
  const st = store();
  const conv = st.conversations.find((c) => c.id === id);
  if (!conv) return Response.json({ error: "not found" }, { status: 404 });
  return Response.json({
    conversation: conv,
    client: st.clients.find((cl) => cl.id === conv.clientId),
    messages: convMessages(id),
    handoff: st.handoffs.find((h) => h.convId === id),
    decisions: st.decisions.filter((d) => d.convId === id),
    deal: st.deals.find((d) => d.clientId === conv.clientId && d.stage !== "Закрыто"),
  });
}
