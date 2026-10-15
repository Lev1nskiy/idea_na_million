// Демо-данные (аналог data/*.json и data/lexicon.json из плана).
import type { CatalogItem, ClientRecord, Policy, QuoteTemplate } from "@/lib/types";

export const POLICY_SEED: Omit<Policy, "version" | "author" | "updatedAt"> = {
  discounts: {
    max_pct: 15,
    tiers: [
      { min_sum: 50_000, pct: 5 },
      { min_sum: 150_000, pct: 12 },
    ],
    bundle_max_pct: 15,
    loyal_bonus_pct: 3,
  },
  warranty: { replace_hours: 24, requires_confirmed_order: true },
  delivery_compensation: [
    { delay_days_gt: 2, pct_of_sum: 5 },
    { delay_days_gt: 5, pct_of_sum: 10 },
  ],
  remedy_options: ["замена", "скидка", "бонус"],
  manager_sla: {
    response_hours: 1,
    off_hours_response_hours: 4,
    work_hours: { from: "09:00", to: "19:00", days: [1, 2, 3, 4, 5] },
  },
  thresholds: {
    min_confidence: 0.7,
    discount_push_limit: 2,
    negative_sentiment: -0.5,
    hold_ping_minutes: 10,
    reminder_minutes: 15,
  },
};

export const CATALOG: CatalogItem[] = [
  { sku: "MF-401A", name: "МФУ ЛазерФокс MF-401A", price: 28_900, stock: 12, lead_time_days: 2, specs: "А4, печать/скан/копир, 38 стр/мин, дуплекс, сеть", compat: ["Windows", "Linux", "1С"] },
  { sku: "LP-220", name: "Принтер ЛазерФокс LP-220", price: 14_500, stock: 40, lead_time_days: 1, specs: "А4 монохром, 30 стр/мин, USB/Ethernet", compat: ["Windows", "macOS", "1С"] },
  { sku: "MF-510C", name: "МФУ цветное ЛазерФокс MF-510C", price: 46_700, stock: 6, lead_time_days: 3, specs: "А4 цвет, 26 стр/мин, дуплекс, Wi-Fi", compat: ["Windows", "macOS"] },
  { sku: "PR-A3P", name: "Принтер А3 ЛазерФокс PRO A3", price: 88_000, stock: 3, lead_time_days: 5, specs: "А3 монохром, 45 стр/мин, 2 лотка", compat: ["Windows", "Linux"] },
  { sku: "MF-770", name: "МФУ производственное ЛазерФокс MF-770", price: 210_000, stock: 1, lead_time_days: 10, specs: "А3, 70 стр/мин, финишер-опция, нагрузка 80k/мес", compat: ["Windows", "Linux", "1С"] },
  { sku: "SC-800", name: "Сканер СканФокс SC-800", price: 21_000, stock: 9, lead_time_days: 2, specs: "Протяжный, 60 стр/мин, АДФ 80 листов", compat: ["Windows"] },
  { sku: "LBL-200", name: "Принтер этикеток ЛейблФокс LBL-200", price: 17_300, stock: 15, lead_time_days: 1, specs: "Термопечать, 203 dpi, USB/Bluetooth", compat: ["Windows", "1С"] },
  { sku: "SHR-150", name: "Шредер ШредФокс SHR-150", price: 9_800, stock: 21, lead_time_days: 1, specs: "4-й уровень секретности, 15 листов", compat: [] },
  { sku: "UPS-1000", name: "ИБП ПауэрФокс UPS-1000", price: 12_400, stock: 14, lead_time_days: 1, specs: "1000 ВА / 600 Вт, LCD, 4 розетки", compat: [] },
  { sku: "CRG-712", name: "Картридж ЛазерФокс CRG-712", price: 3_900, stock: 120, lead_time_days: 0, specs: "Для LP-220, ресурс 1500 стр.", compat: ["LP-220"] },
  { sku: "CRG-725", name: "Картридж ЛазерФокс CRG-725", price: 4_200, stock: 80, lead_time_days: 0, specs: "Для MF-401A / MF-510C, ресурс 2100 стр.", compat: ["MF-401A", "MF-510C"] },
  { sku: "FIN-500", name: "Финишер ЛазерФокс FIN-500", price: 54_000, stock: 2, lead_time_days: 7, specs: "Сортировка, степлирование, для MF-770", compat: ["MF-770"] },
  { sku: "PAP-A4", name: "Бумага А4 офисная (коробка 5 пачек)", price: 1_850, stock: 300, lead_time_days: 0, specs: "80 г/м², класс C", compat: [] },
  { sku: "SRV-PRO", name: "Сервисный контракт «Про» на 12 мес.", price: 36_000, stock: 99, lead_time_days: 0, specs: "Выезд инженера, ТО 4 раза/год, расходники −10%", compat: [] },
];

export const CLIENTS: ClientRecord[] = [
  {
    id: "C-1001", name: "ООО «ГрандСнаб»", contact: "Ирина Лебедева", phone: "+7 921 334-88-12", email: "lebedeva@grandsnab.ru",
    segment: "std", note: "закупает расходники ежеквартально",
    orders: [
      { id: "ЗК-1101", sku: "MF-401A", product: "МФУ ЛазерФокс MF-401A", serial: "SN-88412", purchasedAt: "2025-11-03", status: "delivered", sum: 28_900 },
      { id: "ЗК-1188", sku: "CRG-725", product: "Картридж ЛазерФокс CRG-725 ×4", purchasedAt: "2026-01-20", status: "delivered", sum: 16_800 },
    ],
  },
  {
    id: "C-1002", name: "ООО «Вектор Трейд»", contact: "Павел Сорокин", phone: "+7 812 600-45-90", email: "p.sorokin@vectortrade.ru",
    segment: "std", note: "принтеров в истории нет — гарантийный кейс «заказ не найден»",
    orders: [
      { id: "ЗК-1206", sku: "PAP-A4", product: "Бумага А4 ×10 коробок", purchasedAt: "2026-02-11", status: "delivered", sum: 18_500 },
    ],
  },
  {
    id: "C-1003", name: "АО «НордБанк»", contact: "Марина Северова", phone: "+7 495 777-03-21", email: "m.severova@nordbank.ru",
    segment: "corp", note: "крупный корпоративный клиент, чувствительные бюджеты (Ст. 4)",
    orders: [
      { id: "ЗК-1330", sku: "MF-770", product: "МФУ ЛазерФокс MF-770 + финишер", purchasedAt: "2026-01-15", status: "delivered", sum: 264_000 },
      { id: "ЗК-1402", sku: "SRV-PRO", product: "Сервисный контракт «Про»", purchasedAt: "2026-01-15", status: "delivered", sum: 36_000 },
    ],
  },
  {
    id: "C-1004", name: "ИП Соколова А. В.", contact: "Анна Соколова", phone: "+7 911 208-71-55", email: "sokolova.ip@mail.ru",
    segment: "std", note: "доставка заказа просрочена на 3 дня",
    orders: [
      { id: "ЗК-2044", sku: "LP-220", product: "Принтер ЛазерФокс LP-220 ×2", purchasedAt: "2026-06-22", status: "overdue", sum: 29_000, etaDaysLate: 3 },
    ],
  },
  {
    id: "C-1005", name: "ООО «Альфа Принт»", contact: "Дмитрий Глаголев", phone: "+7 921 555-19-40", email: "d.glagolev@alfaprint.ru",
    segment: "new", note: "новый лид, истории заказов нет",
    orders: [],
  },
  {
    id: "C-1006", name: "ООО «Меридиан Логистика»", contact: "Ольга Ратнер", phone: "+7 812 318-94-07", email: "oratner@meridianlog.ru",
    segment: "loyal", note: "постоянный клиент, бюджет на оснащение до 450 000 ₽",
    orders: [
      { id: "ЗК-1518", sku: "MF-401A", product: "МФУ ЛазерФокс MF-401A ×5", purchasedAt: "2025-09-30", status: "delivered", sum: 137_450 },
    ],
  },
];

export const QUOTE_TEMPLATES: QuoteTemplate[] = [
  {
    id: "QT-OFFICE20", name: "Офис на 20 человек", people: 20,
    items: [
      { sku: "MF-401A", qty: 3 },
      { sku: "LP-220", qty: 6 },
      { sku: "PAP-A4", qty: 20 },
      { sku: "SRV-PRO", qty: 1 },
    ],
    note: "рабочие места + 2 точки печати на отдел",
  },
  {
    id: "QT-OFFICE40", name: "Офис на 40 человек", people: 40,
    items: [
      { sku: "MF-510C", qty: 2 },
      { sku: "MF-401A", qty: 4 },
      { sku: "LP-220", qty: 12 },
      { sku: "UPS-1000", qty: 8 },
      { sku: "PAP-A4", qty: 40 },
      { sku: "SRV-PRO", qty: 2 },
    ],
    note: "два этажа, резерв ИБП на критичные места",
  },
];

// --- Лексикон pre-classifier (data/lexicon.json): маркеры спора / угроз / агрессии ---

export const DISPUTE_MARKERS: { re: RegExp; tag: string }[] = [
  { re: /верните\s+деньги|возврат\s+(денег|средств)|вернуть\s+деньги/i, tag: "refund" },
  { re: /обман(ули|)?|обвели|надули|кинули/i, tag: "deception" },
  { re: /мошенник|аферист/i, tag: "fraud_accusation" },
  { re: /в\s+суд|судебн|претензи|исковое|досудебн/i, tag: "legal" },
  { re: /прокуратур|роспотребнадзор|полици(ю|я)|заявление\s+напишу/i, tag: "authorities" },
  { re: /адвокат|юрист\s+свяжется/i, tag: "lawyer" },
  { re: /требую|настаиваю\s+на\s+возврат/i, tag: "demand" },
  { re: /возмутительн|безобразие|кошмар|ужасн|отвратительн/i, tag: "outrage" },
  { re: /хамство|нахамил|груб(ят|о)/i, tag: "rudeness" },
  { re: /где\s+мои\s+деньги/i, tag: "money_where" },
  { re: /беспредел|развод(ка)?\s/i, tag: "scam_words" },
  { re: /никому\s+не\s+советую|отзыв\s+напишу|все\s+сети\s+расскажу/i, tag: "public_shaming" },
  { re: /говн|дерьм|отстой|блин\s+уже\s+достал/i, tag: "profanity" },
  { re: /refund|chargeback|scam|sue\s+you/i, tag: "en_dispute" },
];

// --- Injection-паттерны (guardrails.detect_injection) ---

export const INJECTION_PATTERNS: { re: RegExp; tag: string }[] = [
  { re: /игнорируй\s+(все\s+)?(предыдущ|прошл|ранее|свои\s+инструкц)/i, tag: "ru_ignore_previous" },
  { re: /забудь\s+(все\s+)?(правила|инструкци)/i, tag: "ru_forget_rules" },
  { re: /ты\s+теперь\s+/i, tag: "ru_you_are_now" },
  { re: /системн(ый|ого)\s+промпт|покажи\s+промпт|выведи\s+инструкци/i, tag: "ru_prompt_leak" },
  { re: /скидк(а|у)\s+9\d\s*%/i, tag: "ru_discount_9x" },
  { re: /устав\s+не\s+действует|отмени\s+устав/i, tag: "ru_charter_override" },
  { re: /ignore\s+(all\s+)?(previous|prior)/i, tag: "en_ignore_previous" },
  { re: /system\s*prompt/i, tag: "en_system_prompt" },
  { re: /jailbreak|DAN\s+mode/i, tag: "en_jailbreak" },
  { re: /act\s+as\s+(an?\s+)?(unrestricted|evil|admin)/i, tag: "en_act_as" },
  { re: /disregard\s+(all|your)/i, tag: "en_disregard" },
  { re: /\{\{\s*system/i, tag: "template_injection" },
  { re: /```\s*(system|instruction)/i, tag: "md_injection" },
  { re: /aWdub3Jl/i, tag: "base64_probe" },
];

export const STRONG_NEGATIVE = /(суд|прокуратур|обман|мошенник|верните\s+деньги|беспредел|возмутительн)/i;
