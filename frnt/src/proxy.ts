import { NextResponse, type NextRequest } from "next/server";

// Передний контур доступа: без сессии страницы уводят на экран входа, API отвечает 401.
// Проверка здесь — только «есть ли токен»: настоящую проверку подписи/роли делает бэкенд
// (core/auth.py), поэтому правила доступа не дублируются в двух местах.

const SESSION_COOKIE = "los_session";
const PUBLIC_PATHS = ["/login", "/api/health", "/api/auth/login", "/api/auth/logout"];

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const authorized = Boolean(request.cookies.get(SESSION_COOKIE)?.value);

  if (PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    return NextResponse.next();
  }

  if (authorized) return NextResponse.next();

  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ detail: "Нужна авторизация: войдите по логину и паролю" }, { status: 401 });
  }

  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname)}`;
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:png|jpg|svg|ico|webp)$).*)"],
};
