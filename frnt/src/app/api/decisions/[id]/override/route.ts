import { logEvent, store } from "@/server/store";

export const dynamic = "force-dynamic";

/** Переопределение автономного решения (Ст. 5): любая авто-операция отменяется человеком без штрафов. */
export async function POST(req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const body = (await req.json()) as { reason?: string; by?: string };
  const st = store();
  const dec = st.decisions.find((d) => d.id === id);
  if (!dec) return Response.json({ error: "not found" }, { status: 404 });
  dec.overriddenBy = body.by ?? "Менеджер А.";
  dec.overrideReason = body.reason ?? "без указания причины";
  logEvent("override", `решение ${id} (${dec.category}) переопределено: ${dec.overrideReason}`, { convId: dec.convId, category: dec.category });
  return Response.json({ ok: true, decision: dec });
}
