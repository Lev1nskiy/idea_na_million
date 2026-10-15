// Тонкий клиент поверх REST API (аналог ui/api_client.py — бизнес-логики в UI нет).
import type {
  BotResponse, ClientRecord, Conversation, ConvStatus, Deal, DecisionRec, EventRec,
  HandoffRec, Message, MetricsReport, NotificationRec, Policy, PolicyVersionMeta, Role,
  SecurityIncident, SiteUser,
} from "@/lib/types";

const j = async <T>(r: Response): Promise<T> => {
  if (!r.ok) throw new Error(await r.text());
  return r.json() as Promise<T>;
};

// --- сессия в браузере ---
// В обычном развёртывании токен лежит только в httpOnly-cookie, и JS его не видит (XSS не уводит
// сессию). Но предпросмотр открывает сайт во фрейме на чужом домене — там браузеры режут
// сторонние cookie, поэтому внутри фрейма дублируем токен в sessionStorage и шлём заголовком
// X-Los-Token: сервер принимает оба канала (src/lib/session.ts).
const TOKEN_KEY = "los_token";
const EMBEDDED =
  typeof window !== "undefined" &&
  (() => {
    try {
      return window.self !== window.top;
    } catch {
      return true; // доступ к window.top запрещён — значит, точно внутри фрейма
    }
  })();

let memoryToken: string | null = null;

function storedToken(): string | null {
  if (!EMBEDDED) return null;
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // приватный режим или запрет хранилища — обходимся памятью
  }
}

/** Запомнить токен сессии (после входа) или забыть его (после выхода/401). */
export function setSessionToken(token: string | null) {
  memoryToken = token;
  if (!EMBEDDED) return;
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch { /* хранилище недоступно — токен остаётся в памяти вкладки */ }
}

function tokenHeaders(): Record<string, string> {
  const token = memoryToken ?? storedToken();
  return token ? { "x-los-token": token } : {};
}

/** Обработчик 401: сессия истекла или отозвана — гасим её и уводим на экран входа. */
async function onUnauthorized(path: string) {
  if (path.includes("/api/auth/login") || path.includes("/api/auth/logout")) return;
  setSessionToken(null);
  try {
    await siteFetch(`${api.base}/api/auth/logout`, { method: "POST" });
  } catch { /* сети нет — cookie всё равно не наша забота */ }
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    const back = window.location.pathname + window.location.search;
    window.location.replace(`/login?next=${encodeURIComponent(back)}`);
  }
}

/** Обёртка над fetch: добавляет токен предпросмотра и единообразно реагирует на 401. */
async function siteFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = { ...(init.headers as Record<string, string> | undefined), ...tokenHeaders() };
  const res = await fetch(path, { ...init, headers });
  if (res.status === 401) void onUnauthorized(path);
  return res;
}

export interface ConvListItem extends Conversation {
  client?: ClientRecord;
  lastAt: string;
  messageCount: number;
  waitingMinutes: number;
}

export interface ConvDetail {
  conversation: Conversation;
  client: ClientRecord;
  messages: Message[];
  handoff?: HandoffRec;
  decisions: DecisionRec[];
  deal?: Deal;
}

export interface NotificationDto extends NotificationRec {
  clientName: string;
  convStatus?: ConvStatus;
  waitingMinutes: number;
}

export interface MetricsDto extends MetricsReport {
  events: EventRec[];
  decisions: (DecisionRec & { clientName: string })[];
}

export interface SimulateResult {
  policyVersion: number;
  discount: { pct: number; reason: string };
  discountAmount: number;
  compensation: { pct: number; amount: number } | null;
  sla: { isWorkTime: boolean; hours: number; label: string };
}

export const api = {
  base: process.env.NEXT_PUBLIC_API_BASE ?? "",
  // --- сотрудники и вход ---
  me: () => siteFetch(`${api.base}/api/auth/me`).then((r) => j<{ user: SiteUser }>(r)),
  login: (login: string, password: string) =>
    siteFetch(`${api.base}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ login, password }),
    })
      .then((r) => j<{ token: string; user: SiteUser }>(r))
      .then((data) => {
        setSessionToken(data.token);
        return data;
      }),
  logout: () =>
    siteFetch(`${api.base}/api/auth/logout`, { method: "POST" })
      .then((r) => j<{ ok: boolean }>(r))
      .finally(() => setSessionToken(null)),
  demoReset: () => siteFetch(`${api.base}/api/demo/reset`, { method: "POST" }).then((r) => j<{ ok: boolean }>(r)),
  users: () => siteFetch(`${api.base}/api/users`).then((r) => j<{ users: SiteUser[] }>(r)),
  createUser: (input: { login: string; password: string; name?: string; role?: Role }) =>
    siteFetch(`${api.base}/api/users`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }).then((r) => j<{ user: SiteUser }>(r)),
  patchUser: (id: string, patch: { name?: string; role?: Role; password?: string; isActive?: boolean }) =>
    siteFetch(`${api.base}/api/users/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).then((r) => j<{ user: SiteUser }>(r)),
  deleteUser: (id: string) => siteFetch(`${api.base}/api/users/${id}`, { method: "DELETE" }).then((r) => j<{ user: SiteUser }>(r)),
  conversations: (status?: string) =>
    siteFetch(`${api.base}/api/conversations${status ? `?status=${status}` : ""}`).then((r) => j<{ conversations: ConvListItem[] }>(r)),
  conversation: (id: string) => siteFetch(`${api.base}/api/conversations/${id}`).then((r) => j<ConvDetail>(r)),
  convMessages: (id: string, after?: string) =>
    siteFetch(`${api.base}/api/conversations/${id}/messages${after ? `?after=${encodeURIComponent(after)}` : ""}`).then((r) => j<{ messages: Message[] }>(r)),
  sendMessage: (clientId: string, text: string) =>
    siteFetch(`${api.base}/api/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ client_id: clientId, channel: "web", text }) }).then((r) => j<BotResponse>(r)),
  takeover: (id: string) => siteFetch(`${api.base}/api/conversations/${id}/takeover`, { method: "POST" }).then((r) => j<{ ok: boolean }>(r)),
  managerReply: (id: string, text: string) =>
    siteFetch(`${api.base}/api/conversations/${id}/manager-reply`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) }).then((r) => j<{ ok: boolean }>(r)),
  resolve: (id: string) => siteFetch(`${api.base}/api/conversations/${id}/resolve`, { method: "POST" }).then((r) => j<{ ok: boolean }>(r)),
  overrideDecision: (id: string, reason: string) =>
    siteFetch(`${api.base}/api/decisions/${id}/override`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason }) }).then((r) => j<{ ok: boolean }>(r)),
  policy: () => siteFetch(`${api.base}/api/policy`).then((r) => j<{ policy: Policy }>(r)),
  putPolicy: (p: Omit<Policy, "version" | "author" | "updatedAt">) =>
    siteFetch(`${api.base}/api/policy`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...p, author: "Администратор" }) }).then((r) => j<{ ok: boolean; policy: Policy; summary: string }>(r)),
  policyHistory: () => siteFetch(`${api.base}/api/policy/history`).then((r) => j<{ history: PolicyVersionMeta[] }>(r)),
  simulate: (args: { sum: number; qty: number; segment: string; isBundle: boolean; delayDays?: number; orderSum?: number }) =>
    siteFetch(`${api.base}/api/policy/simulate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args) }).then((r) => j<SimulateResult>(r)),
  notifications: () => siteFetch(`${api.base}/api/notifications`).then((r) => j<{ notifications: NotificationDto[]; unread: number }>(r)),
  readNotification: (id: string) => siteFetch(`${api.base}/api/notifications/${id}/read`, { method: "POST" }).then((r) => j<{ ok: boolean }>(r)),
  deals: () => siteFetch(`${api.base}/api/deals`).then((r) => j<{ deals: (Deal & { clientName: string })[] }>(r)),
  metrics: () => siteFetch(`${api.base}/api/metrics`).then((r) => j<MetricsDto>(r)),
  incidents: () => siteFetch(`${api.base}/api/security/incidents`).then((r) => j<{ incidents: SecurityIncident[] }>(r)),
  clients: () => siteFetch(`${api.base}/api/clients`).then((r) => j<{ clients: ClientOption[] }>(r)),
};

export type ClientOption = { id: string; name: string; contact: string; segment: string };

/** Резервный справочник: используется, пока страница не получила список с бэкенда. */
export const CLIENT_OPTIONS: ClientOption[] = [
  { id: "C-1005", name: "ООО «Альфа Принт»", contact: "Дмитрий", segment: "новый" },
  { id: "C-1006", name: "ООО «Меридиан Логистика»", contact: "Ольга", segment: "постоянный" },
  { id: "C-1001", name: "ООО «ГрандСнаб»", contact: "Ирина", segment: "стандарт" },
  { id: "C-1002", name: "ООО «Вектор Трейд»", contact: "Павел", segment: "стандарт" },
  { id: "C-1003", name: "АО «НордБанк»", contact: "Марина", segment: "корпоративный" },
  { id: "C-1004", name: "ИП Соколова А. В.", contact: "Анна", segment: "стандарт" },
];
