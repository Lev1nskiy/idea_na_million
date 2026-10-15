import { NextResponse } from "next/server";
import { proxySite } from "@/server/backend";
import { byToken, createUser, listUsers, publicUser } from "@/server/users";
import { sessionToken, type Role } from "@/lib/session";

export const dynamic = "force-dynamic";

/**
 * Сотрудники CRM. Раздел админский: проверка роли дублируется здесь и на бэкенде
 * (интерфейс лишь не показывает то, что всё равно запрещено).
 */
async function guard() {
  const proxied = await proxySite("/users");
  if (proxied) return { proxied, user: null };
  const user = byToken(await sessionToken());
  return { proxied: null, user };
}

export async function GET() {
  const { proxied, user } = await guard();
  if (proxied) return NextResponse.json(await proxied.json(), { status: proxied.status });
  if (!user) return NextResponse.json({ detail: "Нужна авторизация" }, { status: 401 });
  if (user.role !== "admin") return NextResponse.json({ detail: "Доступно только администратору" }, { status: 403 });
  return NextResponse.json({ users: listUsers().map(publicUser), engine: "mock" });
}

export async function POST(req: Request) {
  const raw = await req.text();
  const proxied = await proxySite("/users", { method: "POST", body: raw });
  if (proxied) return NextResponse.json(await proxied.json(), { status: proxied.status });

  const me = byToken(await sessionToken());
  if (!me) return NextResponse.json({ detail: "Нужна авторизация" }, { status: 401 });
  if (me.role !== "admin") return NextResponse.json({ detail: "Доступно только администратору" }, { status: 403 });

  const body = JSON.parse(raw || "{}") as { login?: string; password?: string; name?: string; role?: Role };
  const created = createUser({
    login: body.login ?? "",
    password: body.password ?? "",
    name: body.name,
    role: body.role,
  });
  if (typeof created === "string") return NextResponse.json({ detail: created }, { status: 400 });
  return NextResponse.json({ user: publicUser(created), engine: "mock" });
}
