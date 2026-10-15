import { NextResponse } from "next/server";
import { proxySite } from "@/server/backend";
import { login as mockLogin } from "@/server/users";
import { SESSION_COOKIE, cookieOptions, isHttpsRequest, type SiteUser } from "@/lib/session";

export const dynamic = "force-dynamic";

/**
 * Вход сотрудника. Пароль проверяет бэкенд (PBKDF2-хеши лежат только там), сайт лишь
 * сохраняет выданный токен в httpOnly-cookie — в JS он не попадает.
 * Если бэкенд недоступен, вход обслуживают демо-учётки из src/server/users.ts.
 */
export async function POST(req: Request) {
  const raw = await req.text();
  const proxied = await proxySite("/auth/login", { method: "POST", body: raw });

  let payload: Record<string, unknown>;
  let status = 200;

  if (proxied) {
    status = proxied.status;
    payload = (await proxied.json()) as Record<string, unknown>;
  } else {
    const { login, password } = JSON.parse(raw || "{}") as { login?: string; password?: string };
    const result = mockLogin((login ?? "").trim().toLowerCase(), password ?? "");
    if (result === "bad") {
      status = 401;
      payload = { detail: "Неверный логин или пароль" };
    } else if (result === "inactive") {
      status = 403;
      payload = { detail: "Учётка отключена администратором" };
    } else {
      const { secret: _secret, ...user } = result.user;
      payload = { token: result.token, user, engine: "mock" };
    }
  }

  const res = NextResponse.json(payload, { status });
  if (status === 200) {
    const token = String(payload.token ?? "");
    const user = payload.user as SiteUser | undefined;
    if (token) res.cookies.set(SESSION_COOKIE, token, cookieOptions(isHttpsRequest(req)));
    if (user) res.headers.set("x-user-role", user.role);
  }
  return res;
}
