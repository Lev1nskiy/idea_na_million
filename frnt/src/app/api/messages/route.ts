import { processIncoming } from "@/server/engine";
import { tick } from "@/server/store";
import type { Channel } from "@/lib/types";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  tick();
  const body = (await req.json()) as { client_id?: string; channel?: Channel; text?: string };
  if (!body.client_id || !body.text) {
    return Response.json({ error: "client_id и text обязательны" }, { status: 400 });
  }
  const result = await processIncoming(body.client_id, body.channel ?? "web", body.text);
  return Response.json(result);
}
