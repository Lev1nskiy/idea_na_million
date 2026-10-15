import { NextResponse } from "next/server";
import { proxySite } from "@/server/backend";
import { byToken, publicUser } from "@/server/users";
import { sessionToken } from "@/lib/session";

export const dynamic = "force-dynamic";

/** Кто вошёл: по этой ручке интерфейс рисует роль и прячет недоступные разделы. */
export async function GET() {
  const proxied = await proxySite("/auth/me");
  if (proxied) {
    return NextResponse.json(await proxied.json(), { status: proxied.status });
  }
  const user = byToken(await sessionToken());
  if (!user) return NextResponse.json({ detail: "Нужна авторизация" }, { status: 401 });
  return NextResponse.json({ user: publicUser(user), engine: "mock" });
}
