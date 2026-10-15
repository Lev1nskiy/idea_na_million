// Мост к Python-бэкенду (FastAPI + SQLite): сайт — витрина, данные и правила — на бэкенде.
//
// Как это работает:
//   браузер → /api/... (этот Next-сервер) → CRM_BACKEND_URL/api/site/... (FastAPI)
// Если бэкенд не задан, недоступен или отвечает 5xx — роут отдаёт прежний mock-движок
// (src/server/*), поэтому демо работает автономно: и с бэкендом, и без него.
//
// Почему прокси, а не прямые запросы из браузера: три запроса на страницах (notifications,
// policy, demo/reset) идут мимо src/lib/api.ts, плюс так не нужны CORS-настройки и адрес
// бэкенда не утекает в клиентский бандл.

const BACKEND = (process.env.CRM_BACKEND_URL ?? "").replace(/\/+$/, "");

/** Настроен ли бэкенд (используется в /api/health и в README для диагностики). */
export const backendConfigured = BACKEND.length > 0;

const TIMEOUT_MS = Number(process.env.CRM_BACKEND_TIMEOUT_MS ?? "3000");

/**
 * Проксирует запрос в бэкенд. `null` означает «работай на mock-фоллбеке»:
 *  * бэкенд не сконфигурирован;
 *  * сеть/таймаут;
 *  * ответ 5xx (бэкенд жив, но сломан — демо не должно падать).
 * Ответы 4xx прокидываются как есть: «диалог не найден» — это ответ, а не повод
 * подменять данные чужим demo-стором.
 */
export async function proxySite(path: string, init?: RequestInit): Promise<Response | null> {
  if (!backendConfigured) return null;
  try {
    const res = await fetch(`${BACKEND}/api/site${path}`, {
      ...init,
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
    if (res.status >= 500) return null;
    return new Response(await res.text(), {
      status: res.status,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  } catch {
    return null;
  }
}

/** Текущее состояние бэкенда для /api/health и шапки интерфейса. */
export async function backendHealth(): Promise<{ ok: boolean; detail: string } | null> {
  const proxied = await proxySite("/health");
  if (!proxied) return null;
  return { ok: proxied.ok, detail: "ai-native-crm" };
}
