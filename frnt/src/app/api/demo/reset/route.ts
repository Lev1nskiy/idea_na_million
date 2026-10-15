import { proxySite } from "@/server/backend";
import { mockAdminGuard } from "@/server/users";
import { resetDemo } from "@/server/store";

export const dynamic = "force-dynamic";

export async function POST() {
  const proxied = await proxySite("/demo/reset", { method: "POST", body: "{}" });
  if (proxied) {
    resetDemo(); // локальный demo-стор держим в том же состоянии: фоллбек не должен «отставать»
    return proxied;
  }

  const denied = await mockAdminGuard(); // сброс демо-данных — действие администратора
  if (denied) return denied;
  resetDemo();

  return Response.json({ ok: true });
}
