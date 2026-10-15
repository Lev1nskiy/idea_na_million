import { store } from "@/server/store";

export const dynamic = "force-dynamic";

export async function GET() {
  return Response.json({ history: store().policyHistory });
}
