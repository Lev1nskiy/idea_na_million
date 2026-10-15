import { computeMetrics } from "@/server/metrics";
import { store, tick } from "@/server/store";

export const dynamic = "force-dynamic";

export async function GET() {
  tick();
  const st = store();
  return Response.json({
    ...computeMetrics(),
    events: st.events.slice(-40).reverse(),
    decisions: st.decisions.slice(-60).reverse().map((d) => ({
      ...d,
      clientName: st.clients.find((cl) => cl.id === st.conversations.find((c) => c.id === d.convId)?.clientId)?.name ?? "—",
    })),
  });
}
