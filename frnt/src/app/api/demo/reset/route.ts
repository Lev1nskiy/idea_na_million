import { resetDemo } from "@/server/store";

export const dynamic = "force-dynamic";

export async function POST() {
  resetDemo();
  return Response.json({ ok: true });
}
