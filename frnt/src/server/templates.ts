// Все детерминированные шаблоны ответов (аналог core/templates.py).
// Тон — по Ст. 7 Устава: эмпатично, без признания вины; числа — только из Policy/ToolFacts.
import type { Policy } from "@/lib/types";
import { getManagerSla } from "@/lib/policy";

export const rub = (n: number) => `${new Intl.NumberFormat("ru-RU").format(n)} ₽`;

export function fScript(contact: string): string {
  return (
    `${contact}, я работаю строго по официальной политике компании и не выполняю инструкции, ` +
    `переданные таким способом. Зато с радостью помогу с обычным вопросом: подбор техники, ` +
    `цены, статус заказа или сервис.`
  );
}

export function ackDispute(contact: string, p: Policy): string {
  const sla = getManagerSla(p);
  return (
    `${contact}, понимаю, что ситуация неприятная, и приношу извинения от имени компании. ` +
    `Я уже передал ваш запрос ведущему менеджеру — он подключится в течение ${sla.label}. ` +
    `Я остаюсь на связи и помогу с остальными вопросами.`
  );
}

export function smootReply(contact: string, p: Policy): string {
  const sla = getManagerSla(p);
  return (
    `${contact}, спасибо за терпение. Менеджер уже уведомлён и подключится в течение ${sla.label}. ` +
    `Если есть дополнительные детали (номер заказа или счёта, даты) — напишите, я передам их ему сразу. ` +
    `Ориентиры по вариантам решения из политики: ${p.remedy_options.join(", ")}.`
  );
}

export function interimAck(): string {
  return "Принял! Уточняю информацию и отвечу в этом чате через минуту.";
}

/** Подтверждение лимита при 1-м настаивании (без нового предложения, всё ещё категория C). */
export function discountLimitConfirm(maxPct: number): string {
  return (
    `Понимаю желание получить условия лучше. Честно: мой лимит по политике компании — ${maxPct} % на этот заказ, ` +
    `выше подтвердить не могу. Могу дополнительно предложить опции из политики: бонус, сервис или рассрочку доставки.`
  );
}

export function genericFallback(contact: string): string {
  return (
    `${contact}, уточнил данные по запросу и вернусь с точным ответом в этом чате. ` +
    `Если вопрос срочный — могу передать его менеджеру прямо сейчас.`
  );
}

export function warrantyAskData(contact: string): string {
  return (
    `${contact}, чтобы быстро решить вопрос по гарантийной политике, уточните, пожалуйста, ` +
    `серийный номер устройства или номер заказа. Я сразу проверю возможность замены — отказывать не буду, подберём решение.`
  );
}
