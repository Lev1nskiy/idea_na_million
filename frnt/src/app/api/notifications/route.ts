import { proxySite } from "@/server/backend";
import { store, tick } from "@/server/store";

export const dynamic = "force-dynamic";

export async function GET() {
  const proxied = await proxySite("/notifications");
  if (proxied) return proxied;

  tick();
  const st = store();
  const list = st.notifications
    .map((n) => {
      const conv = st.conversations.find((c) => c.id === n.convId);
      const client = conv ? st.clients.find((cl) => cl.id === conv.clientId) : undefined;
      return {
        ...n,
        clientName: client?.name ?? "—",
        convStatus: conv?.status,
        waitingMinutes: Math.round((Date.now() - new Date(n.createdAt).getTime()) / 60_000),
      };
    })
    .sort((a, b) => Number(b.active) - Number(a.active) || b.lastNotifiedAt.localeCompare(a.lastNotifiedAt));
  return Response.json({ notifications: list, unread: list.filter((n) => !n.read && n.active).length });
}
