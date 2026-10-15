import { store, tick } from "@/server/store";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  tick();
  const st = store();
  const url = new URL(req.url);
  const status = url.searchParams.get("status");
  const list = st.conversations
    .filter((c) => !status || c.status === status)
    .map((c) => {
      const client = st.clients.find((cl) => cl.id === c.clientId);
      const msgs = st.messages.filter((m) => m.convId === c.id);
      const last = msgs[msgs.length - 1];
      return {
        ...c,
        client,
        lastMessage: last?.text ?? c.lastMessage,
        lastAt: last?.at ?? c.updatedAt,
        messageCount: msgs.length,
        waitingMinutes: c.status === "HOLD" ? Math.round((Date.now() - new Date(c.createdAt).getTime()) / 60_000) : 0,
      };
    })
    .sort((a, b) => b.lastAt.localeCompare(a.lastAt));
  return Response.json({ conversations: list });
}
