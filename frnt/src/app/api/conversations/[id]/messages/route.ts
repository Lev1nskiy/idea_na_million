import { proxySite } from "@/server/backend";
import { convMessages, tick } from "@/server/store";

export const dynamic = "force-dynamic";

export async function GET(req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const proxied = await proxySite(`/conversations/${id}/messages${new URL(req.url).search}`);
  if (proxied) return proxied;

  tick();
  const after = new URL(req.url).searchParams.get("after") ?? "";
  const all = convMessages(id);
  return Response.json({ messages: after ? all.filter((m) => m.at > after) : all });
}
