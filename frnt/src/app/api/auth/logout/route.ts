import { NextResponse } from "next/server";
import { proxySite } from "@/server/backend";
import { logout as mockLogout } from "@/server/users";
import { SESSION_COOKIE, cookieOptions, isHttpsRequest } from "@/lib/session";

export const dynamic = "force-dynamic";

/** Выход: гасим сессию на бэкенде (если он есть) и в любом случае убираем cookie. */
export async function POST(req: Request) {
  const token = req.headers.get("cookie")?.match(new RegExp(`${SESSION_COOKIE}=([^;]+)`))?.[1] ?? null;
  await proxySite("/auth/logout", { method: "POST", body: "{}" });
  if (token) mockLogout(token);

  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, "", { ...cookieOptions(isHttpsRequest(req)), maxAge: 0 });
  return res;
}
