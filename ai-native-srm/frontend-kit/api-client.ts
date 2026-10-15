/**
 * Тонкий клиент к AI-native CRM API (без зависимостей, работает в браузере и в Node 18+).
 *
 * Назначение: чтобы сайту не пришлось угадывать контракт. Все формы ответов соответствуют
 * реальному API (`ai-native-srm/docs/frontend_integration.md`), при изменении бэкенда
 * типы удобно перегенерировать из `openapi.json`.
 *
 * Пример:
 *   const api = new CrmApi("http://localhost:8000");
 *   const conv = await api.sendMessage({ clientId: 1, text: "Сколько стоит PX-M4200?" });
 *   console.log(conv.messages[0].text);
 */
export interface BotMessage {
  text: string;
  phase: "ACK" | "INTERIM" | "FINAL" | "HOLD";
  deterministic: boolean;
}

export interface BotResponse {
  conversation_id: number;
  messages: BotMessage[];
  card_updates: unknown[];
  notifications: unknown[];
  events: unknown[];
}

export interface ConversationRow {
  id: number;
  client_id: number;
  client_name: string;
  channel: string;
  status: "ACTIVE" | "HOLD" | "HANDOFF" | "RESOLVED" | "LOST";
  category: string;
  suspicious: boolean;
  assigned_manager: string | null;
  unread_notifications: number;
  last_text: string | null;
  last_role: string | null;
  last_ts: string | null;
  updated_at: string;
}

export interface ConversationMessage {
  id: number;
  role: "client" | "ai" | "manager" | "system";
  text: string;
  phase: string;
  deterministic: boolean;
  latency_ms: number | null;
  ts: string;
}

export interface DecisionRow {
  id: number;
  conversation_id: number;
  category: string;
  route: "AUTONOMOUS" | "CLARIFY" | "HOLD" | string;
  confidence: number | null;
  policy_version: number;
  reasons: string[];
  action_summary: string | null;
  overridden_by: string | null;
  override_reason: string | null;
  overridden_at: string | null;
  ts: string;
}

export interface NotificationRow {
  id: number;
  type: "DISPUTE" | "SECURITY" | "OVERRIDE_DUE" | "REMINDER" | string;
  conversation_id: number | null;
  client_name: string | null;
  text: string;
  read: boolean;
  active: boolean;
  reminder_level: number;
  waited_min: number | null;
  created_at: string;
  last_notified_at: string | null;
}

export interface ClientRow {
  id: number;
  name: string;
  status: string;
  company: string | null;
}

export interface OutboxItem {
  id: number;
  channel: string;
  conversation_id: number;
  text: string;
  phase: string;
  ts: string;
}

export interface Policy {
  version: number;
  discounts: { max_pct: number; tiers: { min_sum: number; pct: number }[]; bundle_max_pct?: number; loyal_bonus_pct?: number };
  warranty: { replace_hours: number; requires_confirmed_order: boolean };
  delivery_compensation: { delay_days_gt: number; pct_of_sum: number }[];
  remedy_options: string[];
  manager_sla: { response_hours: number; off_hours_response_hours: number; work_hours: object };
  thresholds: Record<string, number>;
}

export interface Metrics {
  generated_at: string;
  conversations: number;
  auto_decision_rate_pct: number | null;
  ttfr: Record<string, { count: number; p50_ms: number | null; p95_ms: number | null; threshold_ms: number; share_under_threshold: number | null }>;
  client_retention_in_disputes: { retention_pct: number | null; disputes: number; lost: number; basis: string };
  price_hallucination: { intercepted: number; delivered_hallucinations: number; note: string };
  injection_resistance: { resistance_pct: number | null; conversations: number; incidents: number; breaches: number };
  off_hours_coverage: { off_hours_pct: number | null };
  manager_response_time: Record<string, unknown>;
  override_rate: { overall_pct: number | null; by_category: Record<string, unknown> };
  pre_classifier_recall: { recall: number; false_positive_rate: number; n_disputes: number; n_neutral: number };
  policy_suggestions: string[];
}

/** Ошибка API: код + тело (FastAPI отвечает {"detail": "..."}). */
export class CrmApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(`CRM API ${status}: ${detail}`);
  }
}

export class CrmApi {
  /**
   * @param baseUrl адрес API без завершающего слэша (при одном origin — "" и относительные пути)
   * @param adminToken нужен только для админ-ручек (/api/admin/*) и чтения telegram_id
   */
  constructor(private baseUrl = "", private adminToken = "") {}

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (this.adminToken) headers["X-Admin-Token"] = this.adminToken;

    const resp = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await resp.text();
    const data = text ? JSON.parse(text) : null;
    if (!resp.ok) throw new CrmApiError(resp.status, data?.detail ?? resp.statusText);
    return data as T;
  }

  // --- Диалог клиента ---------------------------------------------------------------------
  /** Сообщение клиента. Ответ приходит сразу (ACK/FINAL), INTERIM может прийти отдельно. */
  sendMessage(input: { clientId: number; text: string; conversationId?: number; channel?: "web" | "telegram" | "email" }) {
    return this.request<BotResponse>("POST", "/api/messages", {
      client_id: input.clientId,
      conversation_id: input.conversationId ?? null,
      text: input.text,
      channel: input.channel ?? "web",
    });
  }

  /** Поллинг новых сообщений диалога: передавайте id последнего полученного сообщения. */
  messages(conversationId: number, after = 0, limit = 200) {
    return this.request<ConversationMessage[]>("GET", `/api/conversations/${conversationId}/messages?after=${after}&limit=${limit}`);
  }

  /** Ответы, которые ещё не забрал канал (для своего чата передавайте channel=web). */
  outbox(channel = "web", limit = 50) {
    return this.request<OutboxItem[]>("GET", `/api/outbox?channel=${channel}&limit=${limit}`);
  }

  // --- Дашборд менеджера ------------------------------------------------------------------
  conversations(status?: string, limit = 50) {
    const q = new URLSearchParams({ limit: String(limit) });
    if (status) q.set("status", status);
    return this.request<ConversationRow[]>("GET", `/api/conversations?${q}`);
  }

  conversation(id: number) {
    return this.request<{
      conversation: ConversationRow;
      client: ClientRow | null;
      deal: Record<string, unknown> | null;
      handoff: Record<string, unknown> | null;
      decisions: DecisionRow[];
      notifications: NotificationRow[];
    }>("GET", `/api/conversations/${id}`);
  }

  takeover(id: number, manager = "manager") {
    return this.request<unknown>("POST", `/api/conversations/${id}/takeover`, { manager });
  }

  managerReply(id: number, text: string, manager = "manager") {
    return this.request<unknown>("POST", `/api/conversations/${id}/manager-reply`, { manager, text });
  }

  resolve(id: number) {
    return this.request<unknown>("POST", `/api/conversations/${id}/resolve`);
  }

  decisions(limit = 100) {
    return this.request<DecisionRow[]>("GET", `/api/decisions?limit=${limit}`);
  }

  overrideDecision(decisionId: number, reason: string, manager = "manager") {
    return this.request<unknown>("POST", `/api/decisions/${decisionId}/override`, { manager, reason });
  }

  clients(limit = 100) {
    return this.request<ClientRow[]>("GET", `/api/clients?limit=${limit}`);
  }

  deals(limit = 100) {
    return this.request<Record<string, unknown>[]>("GET", `/api/deals?limit=${limit}`);
  }

  updateDeal(dealId: number, patch: Record<string, unknown>) {
    return this.request<unknown>("PUT", `/api/deals/${dealId}`, patch);
  }

  notifications(unreadOnly = true, activeOnly = false) {
    return this.request<NotificationRow[]>("GET", `/api/notifications?unread_only=${unreadOnly}&active_only=${activeOnly}`);
  }

  markNotificationRead(id: number) {
    return this.request<unknown>("POST", `/api/notifications/${id}/read`);
  }

  markAllNotificationsRead() {
    return this.request<unknown>("POST", "/api/notifications/read-all");
  }

  // --- Политика и метрики -----------------------------------------------------------------
  policy() {
    return this.request<Policy>("GET", "/api/policy");
  }

  /** Частичное обновление политики: deep merge и новая версия (аудит сохраняется). */
  updatePolicy(patch: Record<string, unknown>, author = "admin", note = "") {
    return this.request<Policy>("PUT", "/api/policy", { patch, author, note });
  }

  policyHistory() {
    return this.request<unknown[]>("GET", "/api/policy/history");
  }

  metrics() {
    return this.request<Metrics>("GET", "/api/metrics");
  }

  securityIncidents() {
    return this.request<Record<string, unknown>[]> ("GET", "/api/security/incidents");
  }

  health() {
    return this.request<{ status: string; provider: string; policy_version: string }>("GET", "/api/health");
  }

  // --- Админ (нужен adminToken) -----------------------------------------------------------
  exportState() {
    return this.request<Record<string, unknown>>("GET", "/api/admin/export");
  }
}
