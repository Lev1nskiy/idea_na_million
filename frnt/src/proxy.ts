import { NextResponse, type NextRequest } from "next/server";

// Передний контур доступа: без сессии страницы уводят на экран входа, API отвечает 401.
// Проверка здесь — только «есть ли токен»: настоящую проверку подписи/роли делает бэкенд
// (core/auth.py), поэтому правила доступа не дублируются в двух местах.

const SESSION_COOKIE = "los_session";
const TOKEN_HEADER = "x-los-token";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const PUBLIC_PATHS = ["/login", "/api/health", "/api/auth/login", "/api/auth/logout"];

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const authorized = Boolean(
    request.cookies.get(SESSION_COOKIE)?.value || request.headers.get(TOKEN_HEADER),
  );

  if (PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    // Уже вошли — форма входа не нужна.
    if (authorized && pathname === "/login") return NextResponse.redirect(new URL("/", request.url));
    return NextResponse.next();
  }

  if (pathname.startsWith("/api/")) {
    if (!authorized) {
      return NextResponse.json({ detail: "Нужна авторизация: войдите по логину и паролю" }, { status: 401 });
    }
    // Cookie на https отдаётся с sameSite=none (иначе её не видно во фрейме), а это разрешает
    // и чужим сайтам слать запросы «от имени» сотрудника. Изменяющие запросы принимаем только
    // со своего же сайта: заголовок Sec-Fetch-Site ставит сам браузер, подделать его нельзя.
    const site = request.headers.get("sec-fetch-site");
    if (!SAFE_METHODS.has(request.method) && site && site !== "same-origin" && site !== "none") {
      return NextResponse.json({ detail: "Запрос пришёл с чужого сайта" }, { status: 403 });
    }
    return NextResponse.next();
  }

  if (authorized) return NextResponse.next();

  // Обычную загрузку страницы закрываем здесь же. Внутри фрейма cookie недоступны (третьесторонний
  // контекст), поэтому такую загрузку и клиентские переходы пропускаем: сессию проверяют
  // клиентский гейт (Nav + страницы) и серверные 401 на /api/*, где данные и лежат.
  const dest = request.headers.get("sec-fetch-dest");
  if (dest && dest !== "document") return NextResponse.next();

  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname)}`;
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:png|jpg|svg|ico|webp)$).*)"],
};
