import { store } from "@/server/store";

export const dynamic = "force-dynamic";

/** Инциденты: только маскированный текст + hash (Ст. 4). Сырой текст в открытом журнале не хранится. */
export async function GET() {
  const st = store();
  return Response.json({ incidents: [...st.incidents].sort((a, b) => b.at.localeCompare(a.at)) });
}
