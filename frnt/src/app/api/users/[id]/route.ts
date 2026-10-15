import { NextResponse } from "next/server";
import { proxySite } from "@/server/backend";
import { byToken, patchUser, publicUser } from "@/server/users";
import { sessionToken, type Role } from "@/lib/session";

export const dynamic = "force-dynamic";

interface PatchBody {
  name?: string;
  role?: Role;
  password?: string;
  isActive?: boolean;
}

async function apply(id: string, raw: string, method: "PATCH" | "DELETE") {
  const proxied = await proxySite(`/users/${id}`, { method, body: raw });
  if (proxied) return NextResponse.json(await proxied.json(), { status: proxied.status });

  const me = byToken(await sessionToken());
  if (!me) return NextResponse.json({ detail: "Нужна авторизация" }, { status: 401 });
  if (me.role !== "admin") return NextResponse.json({ detail: "Доступно только администратору" }, { status: 403 });
  if (id === me.id) return NextResponse.json({ detail: "Свою роль и активность менять нельзя" }, { status: 400 });

  const patch: PatchBody = method === "DELETE" ? { isActive: false } : JSON.parse(raw || "{}");
  const updated = patchUser(id, patch);
  if (typeof updated === "string") {
    const status = updated === "Сотрудник не найден" ? 404 : 400;
    return NextResponse.json({ detail: updated }, { status });
  }
  return NextResponse.json({ user: publicUser(updated), engine: "mock" });
}

export async function PATCH(req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return apply(id, await req.text(), "PATCH");
}

export async function DELETE(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return apply(id, "{}", "DELETE");
}
