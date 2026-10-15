// Контракты между слоями (аналог core/schemas.py из плана).
// Все типы на английском, комментарии — на русском.

export type Category = "A" | "B" | "C" | "D" | "E" | "F" | "G" | "H" | "I";
export type Phase = "ACK" | "INTERIM" | "FINAL" | "HOLD";
export type ConvStatus = "ACTIVE" | "HOLD" | "HANDOFF" | "RESOLVED";
export type Channel = "web" | "telegram";
export type Route = "AUTONOMOUS" | "SMOOTH_HOLD" | "INJECTION";

// --- Сотрудники и роли ---
export type Role = "admin" | "manager";

export interface SiteUser {
  id: string;
  login: string;
  name: string;
  role: Role;
  isActive: boolean;
  createdAt: string;
  lastLoginAt: string | null;
}

// --- Policy Matrix (ТЗ v5, п. 4.1) ---

export interface DiscountTier {
  min_sum: number;
  pct: number;
}

export interface Policy {
  version: number;
  author: string;
  updatedAt: string;
  discounts: {
    max_pct: number;
    tiers: DiscountTier[];
    bundle_max_pct: number;
    loyal_bonus_pct: number;
  };
  warranty: {
    replace_hours: number;
    requires_confirmed_order: boolean;
  };
  delivery_compensation: { delay_days_gt: number; pct_of_sum: number }[];
  remedy_options: string[];
  manager_sla: {
    /** честный срок реакции в рабочее время, часы */
    response_hours: number;
    /** честный срок вне рабочего времени, часы */
    off_hours_response_hours: number;
    work_hours: { from: string; to: string; days: number[] }; // days: 1=пн … 7=вс
  };
  thresholds: {
    min_confidence: number;
    discount_push_limit: number;
    negative_sentiment: number;
    hold_ping_minutes: number;
    reminder_minutes: number;
  };
}

export interface PolicyVersionMeta {
  version: number;
  author: string;
  at: string;
  summary: string;
}

// --- Предметные данные ---

export interface CatalogItem {
  sku: string;
  name: string;
  price: number;
  stock: number;
  lead_time_days: number;
  specs: string;
  compat: string[];
}

export interface Order {
  id: string;
  sku: string;
  product: string;
  serial?: string;
  purchasedAt: string;
  status: "delivered" | "shipping" | "overdue";
  sum: number;
  etaDaysLate?: number;
}

export interface ClientRecord {
  id: string;
  name: string;
  contact: string;
  phone: string;
  email: string;
  segment: "new" | "std" | "loyal" | "corp";
  note: string;
  orders: Order[];
}

export interface QuoteTemplate {
  id: string;
  name: string;
  people: number;
  items: { sku: string; qty: number }[];
  note: string;
}

// --- Пайплайн ---

export interface PreClassification {
  risk: "NONE" | "I" | "F";
  markers: string[];
  legit_text: string | null;
  discount_request_pct: number | null;
  via: "lexicon" | "none";
}

export interface Understanding {
  intents: Category[];
  entities: {
    product?: string;
    qty?: number;
    budget?: number;
    discount_pct?: number;
    serial?: string;
    people?: number;
  };
  sentiment: number; // -1..1
  confidence: number; // 0..1
  injection_suspected: boolean;
  language: "ru" | "en";
}

export interface Decision {
  category: Category;
  route: Route;
  reasons: string[];
  policy_version: number;
  needs_manager_override: boolean;
  clarify: boolean;
}

export interface Message {
  id: string;
  convId: string;
  role: "client" | "ai" | "manager" | "system";
  text: string;
  phase?: Phase;
  deterministic?: boolean;
  latencyMs?: number;
  category?: Category;
  at: string;
}

export interface Conversation {
  id: string;
  clientId: string;
  channel: Channel;
  status: ConvStatus;
  suspicious: boolean;
  discountOfferMade: boolean;
  discountPushCount: number;
  lowConfStreak: number;
  /** максимальная по приоритету категория, встреченная в диалоге (без F) */
  category: Category | null;
  lastAiPingAt: string | null;
  createdAt: string;
  updatedAt: string;
  lastMessage?: string;
}

export interface Deal {
  id: string;
  clientId: string;
  convId: string;
  product?: string;
  qty?: number;
  budget?: number;
  discountPct?: number;
  sum?: number;
  stage: string;
  note: string;
  updatedAt: string;
}

export interface DecisionRec {
  id: string;
  convId: string;
  category: Category;
  confidence: number;
  policyVersion: number;
  overriddenBy?: string;
  overrideReason?: string;
  createdAt: string;
}

export interface HandoffSummary {
  essence: string;
  demands: string[];
  historyDigest: string[];
  remedyOptions: string[];
  recommendedRange: string;
  sentiment: number;
  urgency: "low" | "medium" | "high";
}

export interface HandoffRec {
  id: string;
  convId: string;
  status: "pending" | "taken" | "resolved";
  takenBy?: string;
  summary: HandoffSummary;
  createdAt: string;
}

export type NotificationType = "DISPUTE" | "SECURITY" | "OVERRIDE_DUE" | "REMINDER";

export interface NotificationRec {
  id: string;
  convId: string;
  type: NotificationType;
  text: string;
  read: boolean;
  active: boolean;
  /** уровень эскалации, растёт с каждым дублированием (п. 0 №17) */
  level: number;
  telegram: boolean;
  slaBreached: boolean;
  createdAt: string;
  lastNotifiedAt: string;
}

export interface SecurityIncident {
  id: string;
  convId: string;
  patterns: string[];
  /** ПДн маскированы, сырой текст не хранится (Ст. 4) */
  rawTextMasked: string;
  textHash: string;
  neutralized: boolean;
  at: string;
  clientName: string;
}

export interface EventRec {
  id: string;
  type: string;
  /** payload уже замаскирован через mask_pii */
  payload: string;
  latencyMs?: number;
  category?: Category;
  convId?: string;
  at: string;
}

export interface GuardrailNote {
  id: string;
  ok: boolean;
  detail: string;
}

export interface PipelineTrace {
  pre: PreClassification;
  und: Understanding;
  decision: Decision;
  guardrails: GuardrailNote[];
  whitelistSize: number;
}

export interface BotResponse {
  conversationId: string;
  messages: Message[];
  trace: PipelineTrace;
}

// --- Метрики (раздел «LEARNING», формулы из ТЗ) ---

export interface MetricsReport {
  policyVersion: number;
  totals: {
    conversations: number;
    autoConversations: number;
    messages: number;
    incidents: number;
    holds: number;
  };
  autoDecisionRate: number; // % — (диалоги A–H / все диалоги) × 100
  ttfrIF: { p50: number; p95: number; shareLt100: number; caughtByPreClassifier: number; caughtByLlm: number };
  ttfrOther: { p50: number; p95: number; shareLt5000: number; interimCount: number };
  disputeRetention: number;
  hallucinationBlocks: { v1: number; v2: number };
  injectionResistance: { total: number; neutralized: number; pct: number };
  offHoursCoverage: number;
  overrideRateByCategory: { category: Category; rate: number; total: number }[];
  preClassifierRecall: number;
  categoryHistogram: { category: Category; count: number }[];
}
