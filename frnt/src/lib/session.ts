// Сессия сотрудника на стороне Next-сервера.
//
// Токен выдаёт бэкенд (FastAPI), а живёт он в httpOnly-cookie: клиентский JavaScript его
// не читает, поэтому XSS не уводит сессию. Серверные роуты берут токен из cookie и передают
// его бэкенду в заголовке X-Auth-Token — браузеру этот токен вообще не нужен.

import { cookies } from "next/headers";

import type { Role, SiteUser } from "@/lib/types";

export const SESSION_COOKIE = "los_session";
export const SESSION_MAX_AGE = 12 * 60 * 60; // столько же, сколько TTL сессии на бэкенде

export type { Role, SiteUser };

/** Токен текущей сессии (или null, если её нет). */
export async function sessionToken(): Promise<string | null> {
  try {
    return (await cookies()).get(SESSION_COOKIE)?.value ?? null;
  } catch {
    return null; // вызов вне контекста запроса (например, на этапе сборки)
  }
}

/** Ограничения cookie: httpOnly, lax (переживает переход по ссылке), secure — только на https. */
export function cookieOptions(isHttps: boolean) {
  return {
    httpOnly: true as const,
    sameSite: "lax" as const,
    secure: isHttps,
    path: "/",
    maxAge: SESSION_MAX_AGE,
  };
}

/** Запрос пришёл по https? В превью/туннелях это видно по x-forwarded-proto. */
export function isHttpsRequest(req: Request): boolean {
  const proto = req.headers.get("x-forwarded-proto");
  if (proto) return proto.split(",")[0].trim() === "https";
  try {
    return new URL(req.url).protocol === "https:";
  } catch {
    return false;
  }
}
