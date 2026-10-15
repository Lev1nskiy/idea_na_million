import { store } from "@/server/store";

export const dynamic = "force-dynamic";

export async function GET() {
  const st = store();
  return Response.json({
    deals: st.deals.map((d) => ({ ...d, clientName: st.clients.find((c) => c.id === d.clientId)?.name ?? "—" })),
  });
}
