import { convMessages, tick } from "@/server/store";

export const dynamic = "force-dynamic";

export async function GET(req: Request, ctx: { params: Promise<{ id: string }> }) {
  tick();
  const { id } = await ctx.params;
  const after = new URL(req.url).searchParams.get("after") ?? "";
  const all = convMessages(id);
  return Response.json({ messages: after ? all.filter((m) => m.at > after) : all });
}
