import { store } from "@/server/store";

export const dynamic = "force-dynamic";

export async function POST(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const n = store().notifications.find((x) => x.id === id);
  if (!n) return Response.json({ error: "not found" }, { status: 404 });
  n.read = true;
  return Response.json({ ok: true });
}
