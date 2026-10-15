import { proxySite } from "@/server/backend";
import { resetDemo } from "@/server/store";

export const dynamic = "force-dynamic";

export async function POST() {
  const proxied = await proxySite("/demo/reset", { method: "POST", body: "{}" });
  resetDemo(); // локальный demo-стор держим в том же состоянии: фоллбек не должен «отставать»
  if (proxied) return proxied;

  return Response.json({ ok: true });
}
