import { proxySite } from "@/server/backend";
import { mockAdminGuard } from "@/server/users";
import { store } from "@/server/store";

export const dynamic = "force-dynamic";

export async function GET() {
  const proxied = await proxySite("/policy/history");
  if (proxied) return proxied;

  const denied = await mockAdminGuard();
  if (denied) return denied;

  return Response.json({ history: store().policyHistory });
}
