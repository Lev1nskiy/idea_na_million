// Сессия сотрудника на стороне Next-сервера.
//
// Токен выдаёт бэкенд (FastAPI), а живёт он в httpOnly-cookie: клиентский JavaScript его
// не читает, поэтому XSS не уводит сессию. Серверные роуты берут токен из cookie и передают
// его бэкенду в заголовке X-Auth-Token — браузеру этот токен вообще не нужен.

import { cookies, headers } from "next/headers";

import type { Role, SiteUser } from "@/lib/types";

export const SESSION_COOKIE = "los_session";
/** Дублирующий канал для контекстов без cookie (предпросмотр сайта внутри фрейма). */
export const TOKEN_HEADER = "x-los-token";
export const SESSION_MAX_AGE = 12 * 60 * 60; // столько же, сколько TTL сессии на бэкенде

export type { Role, SiteUser };

/**
 * Токен текущей сессии (или null, если её нет).
 *
 * Основной канал — httpOnly-cookie. Заголовок `X-Los-Token` нужен только там, где cookie
 * недоступны: предпросмотр открывает сайт во фрейме на чужом домене (третьесторонний
 * контекст), и браузеры либо не отдают такие cookie, либо не принимают их вовсе.
 */
export async function sessionToken(): Promise<string | null> {
  try {
    const fromCookie = (await cookies()).get(SESSION_COOKIE)?.value;
    if (fromCookie) return fromCookie;
  } catch { /* вызов вне контекста запроса (например, на этапе сборки) */ }
  try {
    return (await headers()).get(TOKEN_HEADER) ?? null;
  } catch {
    return null;
  }
}

/**
 * Ограничения cookie: httpOnly всегда, secure — только на https.
 *
 * sameSite: на https ставим "none", потому что предпросмотр и встраивание открывают сайт
 * во фрейме на чужом домене — с "lax" браузер не отправляет такой cookie на запросы внутри
 * фрейма, и вход молча возвращал бы на экран логина. На http "none" запрещён, там "lax".
 */
export function cookieOptions(isHttps: boolean) {
  return {
    httpOnly: true as const,
    sameSite: (isHttps ? "none" : "lax") as "none" | "lax",
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
