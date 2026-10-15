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
  me: () => fetch(`${api.base}/api/auth/me`).then((r) => j<{ user: SiteUser }>(r)),
  login: (login: string, password: string) =>
    fetch(`${api.base}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ login, password }),
    }).then((r) => j<{ token: string; user: SiteUser }>(r)),
  logout: () => fetch(`${api.base}/api/auth/logout`, { method: "POST" }).then((r) => j<{ ok: boolean }>(r)),
  users: () => fetch(`${api.base}/api/users`).then((r) => j<{ users: SiteUser[] }>(r)),
  createUser: (input: { login: string; password: string; name?: string; role?: Role }) =>
    fetch(`${api.base}/api/users`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }).then((r) => j<{ user: SiteUser }>(r)),
  patchUser: (id: string, patch: { name?: string; role?: Role; password?: string; isActive?: boolean }) =>
    fetch(`${api.base}/api/users/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).then((r) => j<{ user: SiteUser }>(r)),
  deleteUser: (id: string) => fetch(`${api.base}/api/users/${id}`, { method: "DELETE" }).then((r) => j<{ user: SiteUser }>(r)),
  conversations: (status?: string) =>
    fetch(`${api.base}/api/conversations${status ? `?status=${status}` : ""}`).then((r) => j<{ conversations: ConvListItem[] }>(r)),
  conversation: (id: string) => fetch(`${api.base}/api/conversations/${id}`).then((r) => j<ConvDetail>(r)),
  convMessages: (id: string, after?: string) =>
    fetch(`${api.base}/api/conversations/${id}/messages${after ? `?after=${encodeURIComponent(after)}` : ""}`).then((r) => j<{ messages: Message[] }>(r)),
  sendMessage: (clientId: string, text: string) =>
    fetch(`${api.base}/api/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ client_id: clientId, channel: "web", text }) }).then((r) => j<BotResponse>(r)),
  takeover: (id: string) => fetch(`${api.base}/api/conversations/${id}/takeover`, { method: "POST" }).then((r) => j<{ ok: boolean }>(r)),
  managerReply: (id: string, text: string) =>
    fetch(`${api.base}/api/conversations/${id}/manager-reply`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) }).then((r) => j<{ ok: boolean }>(r)),
  resolve: (id: string) => fetch(`${api.base}/api/conversations/${id}/resolve`, { method: "POST" }).then((r) => j<{ ok: boolean }>(r)),
  overrideDecision: (id: string, reason: string) =>
    fetch(`${api.base}/api/decisions/${id}/override`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason }) }).then((r) => j<{ ok: boolean }>(r)),
  policy: () => fetch(`${api.base}/api/policy`).then((r) => j<{ policy: Policy }>(r)),
  putPolicy: (p: Omit<Policy, "version" | "author" | "updatedAt">) =>
    fetch(`${api.base}/api/policy`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...p, author: "Администратор" }) }).then((r) => j<{ ok: boolean; policy: Policy; summary: string }>(r)),
  policyHistory: () => fetch(`${api.base}/api/policy/history`).then((r) => j<{ history: PolicyVersionMeta[] }>(r)),
  simulate: (args: { sum: number; qty: number; segment: string; isBundle: boolean; delayDays?: number; orderSum?: number }) =>
    fetch(`${api.base}/api/policy/simulate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args) }).then((r) => j<SimulateResult>(r)),
  notifications: () => fetch(`${api.base}/api/notifications`).then((r) => j<{ notifications: NotificationDto[]; unread: number }>(r)),
  readNotification: (id: string) => fetch(`${api.base}/api/notifications/${id}/read`, { method: "POST" }).then((r) => j<{ ok: boolean }>(r)),
  deals: () => fetch(`${api.base}/api/deals`).then((r) => j<{ deals: (Deal & { clientName: string })[] }>(r)),
  metrics: () => fetch(`${api.base}/api/metrics`).then((r) => j<MetricsDto>(r)),
  incidents: () => fetch(`${api.base}/api/security/incidents`).then((r) => j<{ incidents: SecurityIncident[] }>(r)),
  clients: () => fetch(`${api.base}/api/clients`).then((r) => j<{ clients: ClientOption[] }>(r)),
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
