// Оркестратор пайплайна (аналог core/ai_engine.py): PERCEPTION → UNDERSTANDING → DECISION → ACTION (+ Smooth&Hold).
// Принцип «LLM говорит — код решает»: все числа и решения детерминированы, из tools и Policy.
import { DISPUTE_MARKERS, INJECTION_PATTERNS, STRONG_NEGATIVE } from "@/server/data";
import { calcAllowedDiscount, calcDeliveryCompensation } from "@/lib/policy";
import { maskPii, textHash } from "@/lib/privacy";
import {
  ackDispute, discountLimitConfirm, fScript, genericFallback, rub, smootReply, warrantyAskData,
} from "@/server/templates";
import { convMessages, findClient, logEvent, nextId, notify, store } from "@/server/store";
import type {
  BotResponse, Category, Channel, ClientRecord, Conversation, Decision, GuardrailNote,
  Message, PipelineTrace, PreClassification, Understanding,
} from "@/lib/types";

const PRIORITY: Record<Category, number> = { F: 9, I: 8, G: 7, H: 7, D: 6, E: 5, C: 4, B: 3, A: 2 };
const sim = (min: number, span: number) => Math.round(min + Math.random() * span);

// ---------- PERCEPTION (аналог core/perception.py + guardrails.detect_injection) ----------

function preClassify(text: string): PreClassification {
  const markers: string[] = [];
  for (const p of INJECTION_PATTERNS) if (p.re.test(text)) markers.push(p.tag);
  const discountM = text.match(/скидк\w{0,3}\s+(\d{1,3})\s*%/i) ?? text.match(/(\d{1,3})\s*%\s*скидк\w*/i);
  const discountPct = discountM ? Number(discountM[1]) : null;
  if (markers.length > 0) {
    // вырезаем инъекционные фрагменты: делим на предложения, убираем те, где паттерны сработали
    const parts = text.split(/(?<=[.!?])\s+|;/).filter((s) => !INJECTION_PATTERNS.some((p) => p.re.test(s)) && !/скидк\w*\s+9\d\s*%/i.test(s));
    const legit = parts.join(" ").trim();
    return { risk: "F", markers, legit_text: legit.length >= 8 ? legit : null, discount_request_pct: discountPct, via: "lexicon" };
  }
  const dispute: string[] = [];
  for (const m of DISPUTE_MARKERS) if (m.re.test(text)) dispute.push(m.tag);
  if (dispute.length > 0) {
    return { risk: "I", markers: dispute, legit_text: text, discount_request_pct: discountPct, via: "lexicon" };
  }
  return { risk: "NONE", markers: [], legit_text: text, discount_request_pct: discountPct, via: "none" };
}

// ---------- UNDERSTANDING (аналог core/understanding.py поверх MockLLM) ----------

function matchProduct(text: string) {
  const t = text.toLowerCase();
  const found = store().catalog.find((it) => t.includes(it.sku.toLowerCase()));
  if (found) return found;
  const byKind: [RegExp, string][] = [
    [/мфу|многофункциональн/, "MF-401A"],
    [/цветн.*(мфу|принтер)/, "MF-510C"],
    [/принтер(ы)?\s+этикеток|этикетк/, "LBL-200"],
    [/принтер/, "LP-220"],
    [/сканер/, "SC-800"],
    [/шредер/, "SHR-150"],
    [/ибп|бесперебойник/, "UPS-1000"],
    [/картридж/, "CRG-725"],
    [/бумаг/, "PAP-A4"],
    [/финишер/, "FIN-500"],
    [/сервисн(ый|ого)?\s+контракт/, "SRV-PRO"],
  ];
  for (const [re, sku] of byKind) {
    if (re.test(t)) return store().catalog.find((it) => it.sku === sku);
  }
  return undefined;
}

function analyze(text: string, client: ClientRecord): Understanding {
  const t = text.toLowerCase();
  const intents = new Set<Category>();
  const product = matchProduct(text);
  const qtyM = text.match(/(\d{1,3})\s*(?:шт|штук|принтер|мфу|сканер|устройств|единиц)/i);
  const qty = qtyM ? Number(qtyM[1]) : undefined;
  const pctM = text.match(/(\d{1,3})\s*%/);
  const discountPct = pctM ? Number(pctM[1]) : undefined;
  const serialM = text.match(/SN-(\d{3,})/i);
  const peopleM = text.match(/на\s+(\d{1,3})\s*(?:человек|сотрудник|рабочих\s+мест|мест)/i);
  const budgetM = text.match(/(\d[\d\s]{3,})\s*(?:₽|руб)/);

  const has = (re: RegExp) => re.test(t);
  if (has(/гаранти|не\s+(работает|печатает|сканирует|включается)|сломал|поломал|перестал\w*\s+(работать|печатать|сканировать|включаться)|вышел\s+из\s+строя|сгорел|жу[её]т\s+бумагу|полосы\s+при\s+печати|замените|замена/)) intents.add("G");
  if (has(/доставк|где\s+заказ|где\s+мой\s+заказ|опоздал|задерж|перенос|когда\s+привез|статус\s+заказа/)) intents.add("H");
  if (has(/коммерческ|кп\b|смет|оснаст|оснащени|предложение\s+на/)) intents.add("D");
  if (has(/скидк|подешевле|торг|уступк/) || (pctM !== null && has(/%|скидк|условия|цен/))) intents.add("C");
  if (has(/цен(а|у)|стоимост|сколько\s+стоит|прайс|наличи|есть\s+ли|совместим|подойд[её]т/)) intents.add("A");
  if (has(/купить|заказать|нуж(но|ны)|надо|хотим\s+взять|докупить|оформ/) && (product || qty)) intents.add("B");

  if (product && (intents.has("A") || intents.has("C") || intents.has("B")) && !intents.has("G") && !intents.has("H")) {
    // справочный/торговый контур — уверенность высокая
  }
  if (intents.size === 0) intents.add("E");

  let sentiment = 0.1;
  for (const m of DISPUTE_MARKERS) if (m.re.test(text)) sentiment -= 0.35;
  if (STRONG_NEGATIVE.test(text)) sentiment -= 0.25;
  if (has(/спасибо|благодар|отлично|супер/)) sentiment += 0.3;
  sentiment = Math.max(-1, Math.min(1, sentiment));

  const known = !!product || !!serialM || intents.has("G") || intents.has("H") || intents.has("D");
  const confidence = intents.has("E") && intents.size === 1 ? 0.5 : known ? 0.93 : 0.86;

  return {
    intents: [...intents],
    entities: {
      product: product?.name,
      qty,
      budget: budgetM ? Number(budgetM[1].replace(/\s/g, "")) : undefined,
      discount_pct: discountPct,
      serial: serialM ? `SN-${serialM[1]}` : undefined,
      people: peopleM ? Number(peopleM[1]) : undefined,
    },
    sentiment,
    confidence,
    injection_suspected: false,
    language: /[a-z]/i.test(text) && !/[а-я]/i.test(text) ? "en" : "ru",
  };
}

// ---------- DECISION (аналог core/decision.py) ----------

function decide(pre: PreClassification, und: Understanding, conv: Conversation): Decision {
  const p = store().policy;
  const reasons: string[] = [];
  if (pre.risk === "F") {
    return { category: "F", route: "INJECTION", reasons: [`инъекция: ${pre.markers.join(", ")}`], policy_version: p.version, needs_manager_override: false, clarify: false };
  }
  const rank = (c: Category) => PRIORITY[c];
  const top = und.intents.reduce((a, b) => (rank(b) > rank(a) ? b : a), und.intents[0] ?? "E");
  if (pre.risk === "I") reasons.push(`маркеры спора (lexicon): ${pre.markers.join(", ")}`);
  if (und.sentiment <= p.thresholds.negative_sentiment) reasons.push(`резко негативная тональность ${und.sentiment.toFixed(2)} < ${p.thresholds.negative_sentiment}`);

  const serviceCase = top === "G" || top === "H";
  // граница C/I (Ст. 6, п. 0 №3 и №16): настаивание ≥ лимита или резкий негатив → эскалация
  const discountEscalation =
    (conv.discountPushCount >= p.thresholds.discount_push_limit && (top === "C" || conv.discountOfferMade)) ||
    (top === "C" && und.sentiment <= p.thresholds.negative_sentiment);
  if (!serviceCase && (pre.risk === "I" || und.sentiment <= p.thresholds.negative_sentiment || discountEscalation)) {
    reasons.push(`настаивание #${conv.discountPushCount} (лимит ${p.thresholds.discount_push_limit}) → эскалация`);
    return { category: "I", route: "SMOOTH_HOLD", reasons, policy_version: p.version, needs_manager_override: true, clarify: false };
  }
  if (serviceCase && (pre.risk === "I" || und.sentiment <= p.thresholds.negative_sentiment)) {
    reasons.push(`сервисная категория ${top} приоритетнее спора без угроз`);
  }
  if (und.confidence < p.thresholds.min_confidence) {
    reasons.push(`confidence ${und.confidence.toFixed(2)} < ${p.thresholds.min_confidence} → уточняющий вопрос, а не эскалация`);
    return { category: "E" as Category, route: "AUTONOMOUS", reasons, policy_version: p.version, needs_manager_override: top === "E", clarify: true };
  }
  if (reasons.length === 0) reasons.push(`интент ${top} (уверенность ${und.confidence.toFixed(2)}), приоритет F>I>G/H>D>E>C>B>A`);
  return { category: top, route: "AUTONOMOUS", reasons, policy_version: p.version, needs_manager_override: false, clarify: false };
}

// ---------- ACTION (аналог core/action.py) ----------

interface ActionOut { texts: { text: string; deterministic: boolean; latency: number }[]; guardrails: GuardrailNote[]; }

function runAction(conv: Conversation, client: ClientRecord, und: Understanding, decision: Decision): ActionOut {
  const p = store().policy;
  const g: GuardrailNote[] = [];
  const lat = () => sim(1150, 1250);
  // контекст сделки: продукт/количество из текущего сообщения или из активной карточки (диалог не «забывает»)
  const dealCtx = store().deals.find((d) => d.clientId === client.id && d.stage !== "Закрыто");
  const product = und.entities.product
    ? store().catalog.find((c) => c.name === und.entities.product)
    : dealCtx?.product
      ? store().catalog.find((c) => c.name === dealCtx.product)
      : undefined;
  const qty = Math.max(1, Math.min(60, und.entities.qty ?? dealCtx?.qty ?? 1));
  const ok = (id: string, detail: string) => g.push({ id, ok: true, detail });

  switch (decision.category) {
    case "A": {
      if (product) {
        ok("V1", `числа ∈ whitelist: цена ${product.price}, остаток ${product.stock}, срок ${product.lead_time_days} дн.`);
        return { guardrails: g, texts: [{ deterministic: false, latency: lat(), text:
          `${product.name}: ${rub(product.price)}, в наличии ${product.stock} шт, поставка ${product.lead_time_days} дн. ` +
          `${product.specs}. Совместимость: ${product.compat.length ? product.compat.join(", ") : "не требуется"}. Нужны характеристики подробнее или расчёт на партию?`,
        }] };
      }
      ok("V1", "ответ без цен — только факты базы знаний");
      return { guardrails: g, texts: [{ deterministic: false, latency: lat(), text:
        `${client.contact.split(" ")[0]}, доставка по городу — 1–2 дня, на технику действует гарантия и замена по политике. ` +
        `Подсказать конкретную модель? Например: МФУ ЛазерФокс MF-401A — ${rub(28_900)}.` }] };
    }
    case "B": {
      const sum = product ? product.price * qty : undefined;
      ok("V1", `whitelist: ${qty} шт${sum ? `, сумма ${sum}` : ""} — из сообщения клиента и каталога`);
      return { guardrails: g, texts: [{ deterministic: false, latency: lat(), text:
        `Отмечаю в заявке: ${product ? product.name : "техника"} × ${qty}${sum ? `, ориентир по сумме ${rub(sum)} без скидки` : ""}. ` +
        `Подскажите желаемый срок закупки — и я сразу подготовлю условия со скидкой по политике.` }] };
    }
    case "C": {
      const sum = product ? product.price * qty : 0;
      const allowed = calcAllowedDiscount(p, { qty, orderSum: sum, segment: client.segment, isBundle: qty >= 5 });
      const requested = und.entities.discount_pct;
      // эскалационные ветки уже закрыты в decision; здесь — грант, одно предложение максимума
      // или (при 1-м настаивании) подтверждение лимита без нового предложения (п. 0 №16)
      if (requested && requested > allowed.pct) {
        if (conv.discountOfferMade) {
          ok("V2", "повторное предложение не выдаётся — только подтверждение лимита (Ст. 6)");
          return { guardrails: g, texts: [{ deterministic: true, latency: sim(30, 90), text: discountLimitConfirm(Math.min(allowed.pct || p.discounts.max_pct, p.discounts.max_pct)) }] };
        }
        g.push({ id: "V1", ok: false, detail: `черновик LLM эхом повторил «${requested}%» из сообщения клиента — заблокировано (число вне whitelist) → regenerate` });
        // regenerate: в финальном тексте только разрешённая цифра
        ok("V1", `после regenerate числа ∈ whitelist: max ${allowed.pct}% (Policy v${p.version})`);
        ok("V2", `скидка ≤ max_pct (${p.discounts.max_pct}%)`);
        conv.discountOfferMade = true;
        return { guardrails: g, texts: [{ deterministic: false, latency: lat(), text:
          `${client.contact.split(" ")[0]}, понимаю, что хочется условий лучше. По политике компании я могу подтвердить максимум ` +
          `${allowed.pct} % на этот заказ — экономия ${rub(Math.round((sum * allowed.pct) / 100))}. Предлагаю зафиксировать эти условия: честно говорю, выше подтвердить не могу.` }] };
      }
      if (requested && requested <= allowed.pct) {
        const eff = Math.max(requested, 0);
        ok("V2", `запрошенные ${eff}% ≤ допустимых ${allowed.pct}%`);
        return { guardrails: g, texts: [{ deterministic: false, latency: lat(), text:
          `Подтверждаю скидку ${eff} %${product ? ` на ${product.name} × ${qty}` : ""}. ` +
          (sum ? `Итого к оплате ${rub(Math.round(sum * (1 - eff / 100)))} вместо ${rub(sum)}. ` : "") +
          `Наличие и сроки — из каталога, оформить счёт?` }] };
      }
      if (conv.discountOfferMade) {
        ok("V2", "повторное предложение не выдаётся — только подтверждение лимита (Ст. 6)");
        return { guardrails: g, texts: [{ deterministic: true, latency: sim(30, 90), text: discountLimitConfirm(Math.min(allowed.pct || p.discounts.max_pct, p.discounts.max_pct)) }] };
      }
      // грант по умолчанию (демо «Скидка»)
      const pct = allowed.pct;
      if (pct === 0) {
        const firstTier = [...p.discounts.tiers].sort((a, b) => a.min_sum - b.min_sum)[0];
        ok("V2", `заказ не достиг тира (от ${rub(firstTier?.min_sum ?? 0)}) — скидка 0% по матрице`);
        return { guardrails: g, texts: [{ deterministic: false, latency: lat(), text:
          `${client.contact.split(" ")[0]}, для этого объёма (${product ? `${product.name} × ${qty}, ${rub(sum)}` : `сумма ${rub(sum)}`}) скидка матрицей не предусмотрена — ` +
          `тиры начинаются от ${rub(firstTier?.min_sum ?? 50_000)}. Могу предложить бонусные опции из политики или расчёт на больший объём.` }] };
      }
      ok("V2", `скидка ${pct}% ≤ max_pct ${p.discounts.max_pct}% (${allowed.reason})`);
      return { guardrails: g, texts: [{ deterministic: false, latency: lat(), text:
        product
          ? `Для заказа ${product.name} × ${qty} на сумму ${rub(sum)} по политике действует скидка ${pct} % — к оплате ${rub(Math.round(sum * (1 - pct / 100)))}. ` +
            `В наличии ${product.stock} шт, поставка ${product.lead_time_days} дн. Оформить заявку?`
          : `Подскажите модель и количество — посчитаю скидку по политике точно (сейчас лимит до ${p.discounts.max_pct} % на суммы от ${rub(p.discounts.tiers[p.discounts.tiers.length - 1]?.min_sum ?? 150_000)}).` }] };
    }
    case "D": {
      const people = und.entities.people ?? 20;
      const tpl = store().quotes.find((q) => q.people >= people) ?? store().quotes[0];
      const lines = tpl.items.map((it) => {
        const item = store().catalog.find((c) => c.sku === it.sku)!;
        return { item, qty: it.qty, sum: item.price * it.qty };
      });
      const total = lines.reduce((s, l) => s + l.sum, 0);
      const allowed = calcAllowedDiscount(p, { qty: 10, orderSum: total, segment: client.segment, isBundle: true });
      const final = Math.round(total * (1 - allowed.pct / 100));
      ok("V1", `числа КП из каталога: ${lines.length} позиций, итог ${total}`);
      ok("V2", `скидка КП ${allowed.pct}% ≤ bundle_max ${p.discounts.bundle_max_pct}%`);
      return { guardrails: g, texts: [{ deterministic: false, latency: lat(), text:
        `${client.contact.split(" ")[0]}, подготовил КП «${tpl.name}» (${tpl.note}): ` +
        lines.map((l) => `${l.item.name.replace(/^(МФУ|Принтер|Бумага|Сервисный контракт)\s?/u, (m) => m)} × ${l.qty}`).join(", ") +
        `. Итог ${rub(total)} − скидка ${allowed.pct} % = ${rub(final)}. Отправить полную версию на ${client.email}?` }] };
    }
    case "E": {
      if (decision.clarify) {
        conv.lowConfStreak += 1;
        ok("V6", "низкая уверенность → сбор данных, без выдумывания фактов");
        if (conv.lowConfStreak >= 2 || decision.needs_manager_override) {
          notify(conv.id, "OVERRIDE_DUE", `${client.name}: сложный вопрос, требуется уточнение специалиста.`);
          return { guardrails: g, texts: [{ deterministic: true, latency: sim(200, 400), text:
            `${client.contact.split(" ")[0]}, здесь лучше ответить точно, а не быстро: я уточню у профильного специалиста и вернусь в этом чате. Менеджеру уже оставил задачу.` }] };
        }
        return { guardrails: g, texts: [{ deterministic: true, latency: sim(200, 400), text:
          `${client.contact.split(" ")[0]}, уточните, пожалуйста, детали: о какой модели или сценарии использования речь? Соберу факты — и дам точный ответ.` }] };
      }
      ok("V1", "ответ сформулирован из базы знаний, без чисел");
      return { guardrails: g, texts: [{ deterministic: false, latency: lat(), text:
        `Да, такое возможно: техника интегрируется со стандартными сценариями печати (1С, Windows/сервер печати), ` +
        `настройку берёт на себя наш инженер при пуско-наладке. Если нужна точная схема под вашу инфраструктуру — уточню у специалиста и вернусь.` }] };
    }
    case "G": {
      const serial = und.entities.serial;
      const order = client.orders.find((o) => (serial && o.serial === serial));
      if (order) {
        ok("V1", `числа из Policy: замена ${p.warranty.replace_hours} ч; заказ найден в истории клиента`);
        ok("V3", "формулировки без признания вины (Ст. 6)");
        return { guardrails: g, texts: [{ deterministic: false, latency: lat(), text:
          `${client.contact.split(" ")[0]}, заказ ${order.id} (${order.product}) найден в вашей истории. По гарантийной политике организуем замену ` +
          `в течение ${p.warranty.replace_hours} ч — без лишних вопросов. Заявка на замену уже оформлена, подтверждение придёт в этот чат.` }] };
      }
      notify(conv.id, "OVERRIDE_DUE", `${client.name}: гарантийный запрос, заказ не найден автоматически — проверить вручную.`);
      ok("V7", "без выдуманных сроков: сбор данных вместо отказа");
      return { guardrails: g, texts: [{ deterministic: true, latency: sim(250, 500), text: warrantyAskData(client.contact.split(" ")[0]) }] };
    }
    case "H": {
      const overdue = client.orders.find((o) => o.status === "overdue");
      const delay = overdue?.etaDaysLate ?? 2;
      const comp = overdue ? calcDeliveryCompensation(p, delay, overdue.sum) : null;
      ok("V1", comp ? `компенсация ${comp.pct}% = ${comp.amount} из Policy (задержка ${delay} дн.)` : "компенсация не положена по матрице");
      ok("V3", "извинение без признания вины");
      return { guardrails: g, texts: [{ deterministic: false, latency: lat(), text:
        overdue && comp
          ? `${client.contact.split(" ")[0]}, проверил статус: заказ ${overdue.id} (${overdue.product}) задерживается на ${delay} дня — извините за неудобство. ` +
            `По политике доставки положена компенсация ${comp.pct} % от суммы заказа — ${rub(comp.amount)}. Дополнительно доступно: ${store().policy.remedy_options.join(", ")}. Какой вариант удобнее?`
          : `${client.contact.split(" ")[0]}, проверил: заказ в пути, задержка в пределах нормы. По политике при задержке свыше 2 дней положена компенсация — как только порог будет превышен, оформлю автоматически.` }] };
    }
    default:
      return { guardrails: g, texts: [{ deterministic: true, latency: 40, text: genericFallback(client.contact.split(" ")[0]) }] };
  }
}

// ---------- HANDOFF + основной цикл ----------

function buildHandoff(conv: Conversation, client: ClientRecord, triggerText: string, sentiment: number): void {
  const p = store().policy;
  const demands: string[] = [];
  if (/верните\s+деньги|возврат/i.test(triggerText)) demands.push("возврат средств");
  if (/суд|юрист|претензi|иск/i.test(triggerText)) demands.push("угроза судебного иска");
  if (/скидк|цен/i.test(triggerText)) demands.push("требование по цене");
  if (demands.length === 0) demands.push("эскалация без явного требования");
  const maxComp = p.delivery_compensation[p.delivery_compensation.length - 1];
  store().handoffs.push({
    id: nextId("ho"), convId: conv.id, status: "pending", createdAt: new Date().toISOString(),
    summary: {
      essence: maskPii(triggerText.length > 220 ? triggerText.slice(0, 220) + "…" : triggerText),
      demands,
      historyDigest: client.orders.slice(0, 3).map((o) => `${o.id} · ${o.product} · ${rub(o.sum)}`),
      remedyOptions: p.remedy_options,
      recommendedRange: `скидка до ${p.discounts.max_pct}%; компенсация до ${maxComp?.pct_of_sum ?? 0}% от суммы заказа`,
      sentiment,
      urgency: /суд|юрист|прокуратур/i.test(triggerText) ? "high" : sentiment < -0.6 ? "medium" : "low",
    },
  });
}

function enterHold(conv: Conversation, client: ClientRecord, und: Understanding, triggerText: string, ackText: string, ackLatency: number, deterministic: boolean): Message {
  const st = store();
  conv.status = "HOLD";
  conv.category = "I";
  conv.updatedAt = new Date().toISOString();
  const msg: Message = {
    id: nextId("m"), convId: conv.id, role: "ai", text: ackText, phase: "ACK",
    deterministic, latencyMs: ackLatency, category: "I", at: new Date().toISOString(),
  };
  st.messages.push(msg);
  buildHandoff(conv, client, triggerText, und.sentiment);
  notify(conv.id, "DISPUTE", `${client.name}: спор — ${maskPii(triggerText.slice(0, 90))}… Требуется подключение менеджера.`);
  st.decisions.push({ id: nextId("dec"), convId: conv.id, category: "I", confidence: und.confidence, policyVersion: st.policy.version, createdAt: new Date().toISOString() });
  logEvent("reply", `ACK спора · ${deterministic ? "pre-classifier (<100 мс)" : "после Understanding"}`, { latencyMs: ackLatency, category: "I", convId: conv.id });
  return msg;
}

export async function processIncoming(clientId: string, channel: Channel, rawText: string): Promise<BotResponse> {
  const st = store();
  const client = findClient(clientId);
  if (!client) throw new Error(`unknown client ${clientId}`);
  const text = rawText.trim().slice(0, 4000);
  const t0 = performance.now();

  // открытый диалог клиента или новый
  let conv = st.conversations.find((c) => c.clientId === clientId && c.status !== "RESOLVED");
  if (!conv) {
    conv = {
      id: nextId("dlg"), clientId, channel, status: "ACTIVE", suspicious: false,
      discountOfferMade: false, discountPushCount: 0, lowConfStreak: 0, category: null,
      lastAiPingAt: null, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
    };
    st.conversations.push(conv);
    logEvent("conversation_open", `${client.name} · канал ${channel}`, { convId: conv.id });
  }
  st.messages.push({ id: nextId("m"), convId: conv.id, role: "client", text, at: new Date().toISOString() });
  conv.updatedAt = new Date().toISOString();

  logEvent("message_in", maskPii(text.slice(0, 160)), { convId: conv.id });
  const out: Message[] = [];

  // --- PERCEPTION: pre-classifier до любого LLM (<100 мс) ---
  const pre = preClassify(text);
  const und = analyze(pre.legit_text ?? "", client);

  // F-ветка: детерминированный шаблон без LLM + инцидент; диалог не блокируется
  if (pre.risk === "F") {
    conv.suspicious = true;
    const latency = Math.max(4, Math.round(performance.now() - t0));
    st.incidents.push({
      id: nextId("sec"), convId: conv.id, patterns: pre.markers,
      rawTextMasked: maskPii(text), textHash: textHash(text),
      neutralized: true, at: new Date().toISOString(), clientName: client.name,
    });
    notify(conv.id, "SECURITY", `${client.name}: попытка инъекции (${pre.markers.join(", ")}) — нейтрализована F-скриптом.`);
    const fMsg: Message = {
      id: nextId("m"), convId: conv.id, role: "ai", text: fScript(client.contact.split(" ")[0]),
      phase: "ACK", deterministic: true, latencyMs: latency, category: "F", at: new Date().toISOString(),
    };
    st.messages.push(fMsg);
    out.push(fMsg);
    logEvent("security", `инъекция нейтрализована · ${pre.markers.join(", ")}`, { convId: conv.id });
    logEvent("reply", "ответ F-скриптом · pre-classifier", { latencyMs: latency, category: "F", convId: conv.id });
  }

  const guardrails: GuardrailNote[] = [];
  let decision: Decision;

  if (pre.risk === "I" && conv.status !== "HOLD" && conv.status !== "HANDOFF") {
    // ACK спора без LLM — целевой TTFR <100 мс по диалогам pre-classifier (п. 0 №14)
    const latency = Math.max(4, Math.round(performance.now() - t0));
    und.sentiment = Math.min(und.sentiment, -0.6);
    out.push(enterHold(conv, client, und, text, ackDispute(client.contact.split(" ")[0], st.policy), latency, true));
    decision = { category: "I", route: "SMOOTH_HOLD", reasons: [`lexical pre-classifier: ${pre.markers.join(", ")}`], policy_version: st.policy.version, needs_manager_override: true, clarify: false };
  } else if (conv.status === "HOLD" || conv.status === "HANDOFF") {
    // Smooth-ответы: без обсуждения сути, эмпатия + честный SLA (V6)
    if (conv.status === "HOLD") {
      await sleep(260);
      const msg: Message = {
        id: nextId("m"), convId: conv.id, role: "ai", text: smootReply(client.contact.split(" ")[0], st.policy),
        phase: "HOLD", deterministic: false, latencyMs: sim(900, 900), category: "I", at: new Date().toISOString(),
      };
      st.messages.push(msg);
      conv.lastAiPingAt = msg.at;
      out.push(msg);
      logEvent("reply", "SMOOTH-ответ в HOLD (V6 пройден)", { latencyMs: msg.latencyMs, category: "I", convId: conv.id });
    }
    decision = { category: "I", route: "SMOOTH_HOLD", reasons: ["диалог уже в HOLD: только smoothing, суть — за менеджером"], policy_version: st.policy.version, needs_manager_override: true, clarify: false };
  } else if (conv.status === "ACTIVE") {
    // --- UNDERSTANDING + DECISION + ACTION ---
    await sleep(420); // имитация времени работы LLM-слоя (визуально в чате)
    und.injection_suspected = pre.risk === "F";

    // учёт настаивания по скидке (п. 0 №16)
    const pct = und.entities.discount_pct ?? pre.discount_request_pct ?? null;
    const pushing =
      conv.discountOfferMade &&
      (pct !== null || /нет[,.!\s]|не\s+устраивает|маловато|хочу\s+больше|неприемлем|слишком\s+мало/i.test(text));
    if (pushing && conv.status === "ACTIVE") {
      conv.discountPushCount += 1;
      logEvent("discount_push", `настаивание #${conv.discountPushCount} (лимит ${st.policy.thresholds.discount_push_limit})`, { convId: conv.id });
    }

    // после F-шаблона легитимная часть сообщения обрабатывается обычным пайплайном (п. 0 №5)
    const preForDecision: PreClassification = pre.risk === "F" ? { ...pre, risk: "NONE" } : pre;
    decision = decide(preForDecision, und, conv);
    // обновляем «максимальную категорию диалога» (для auto-decision rate)
    if (decision.category !== "F" && (conv.category === null || PRIORITY[decision.category] > PRIORITY[conv.category])) {
      conv.category = decision.category;
    }

    if (decision.route === "SMOOTH_HOLD") {
      // «тонкий» спор, найденный LLM: ACK сразу после Understanding
      const latency = Math.round(performance.now() - t0) + 150;
      out.push(enterHold(conv, client, und, text, ackDispute(client.contact.split(" ")[0], st.policy), latency, false));
    } else {
      const acted = runAction(conv, client, und, decision);
      guardrails.push(...acted.guardrails);
      for (const t of acted.texts) {
        const msg: Message = {
          id: nextId("m"), convId: conv.id, role: "ai", text: t.text, phase: "FINAL",
          deterministic: t.deterministic, latencyMs: t.latency, category: decision.category, at: new Date().toISOString(),
        };
        st.messages.push(msg);
        out.push(msg);
      }
      conv.lowConfStreak = decision.clarify ? conv.lowConfStreak : 0;
      st.decisions.push({ id: nextId("dec"), convId: conv.id, category: decision.category, confidence: und.confidence, policyVersion: st.policy.version, createdAt: new Date().toISOString() });
      logEvent("reply", `ответ · категория ${decision.category} · Policy v${st.policy.version}`, { latencyMs: acted.texts.reduce((a, x) => Math.max(a, x.latency), 0), category: decision.category, convId: conv.id });
      // карточка сделки (card_updates)
      if (und.entities.product || und.entities.qty) {
        const stage = decision.category === "D" ? "КП отправлено" : decision.category === "C" ? "Переговоры" : decision.category === "G" ? "Сервис" : "Квалификация";
        const existing = st.deals.find((d) => d.clientId === clientId && d.stage !== "Закрыто");
        if (existing) {
          existing.product = und.entities.product ?? existing.product;
          existing.qty = und.entities.qty ?? existing.qty;
          existing.stage = stage;
          existing.updatedAt = new Date().toISOString();
        } else {
          st.deals.push({
            id: nextId("deal"), clientId, convId: conv.id, product: und.entities.product, qty: und.entities.qty,
            stage, note: "", updatedAt: new Date().toISOString(),
          });
        }
      }
      conv.updatedAt = new Date().toISOString();
      conv.lastMessage = out[out.length - 1]?.text.slice(0, 120);
    }
  } else {
    decision = { category: "I", route: "SMOOTH_HOLD", reasons: ["диалог обслуживается менеджером"], policy_version: st.policy.version, needs_manager_override: true, clarify: false };
  }

  const trace: PipelineTrace = { pre, und, decision, guardrails, whitelistSize: 24 };
  return { conversationId: conv.id, messages: out, trace };
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
