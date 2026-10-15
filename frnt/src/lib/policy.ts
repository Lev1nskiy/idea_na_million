// Policy Management — детерминированные расчёты поверх Policy Matrix (аналог core/policy.py).
// ИИ читает актуальную версию на КАЖДЫЙ запрос (сторожится модуль store — кэша нет).
import type { Policy } from "@/lib/types";

export interface DiscountArgs {
  qty: number;
  orderSum: number;
  segment: "new" | "std" | "loyal" | "corp";
  isBundle: boolean;
}

/** Допустимая скидка по матрице: тиры по сумме + бонус лояльности, ограничение крышей. */
export function calcAllowedDiscount(p: Policy, a: DiscountArgs): { pct: number; reason: string } {
  let base = 0;
  for (const t of [...p.discounts.tiers].sort((x, y) => x.min_sum - y.min_sum)) {
    if (a.orderSum >= t.min_sum) base = Math.max(base, t.pct);
  }
  const parts: string[] = [];
  if (base > 0) parts.push(`тир ${base}% от суммы заказа`);
  if (a.segment === "loyal" || a.segment === "corp") {
    base += p.discounts.loyal_bonus_pct;
    parts.push(`бонус постоянного клиента +${p.discounts.loyal_bonus_pct}%`);
  }
  // глобальный потолок матрицы обойти нельзя: даже bundle-лимит ограничен max_pct
  const cap = Math.min(a.isBundle ? p.discounts.bundle_max_pct : p.discounts.max_pct, p.discounts.max_pct);
  const pct = Math.min(base, cap);
  if (a.qty >= 5 && a.isBundle) parts.push(`комплект — лимит min(bundle_max, max) = ${cap}%`);
  return { pct, reason: parts.join(" · ") || "заказ не достиг тира скидки" };
}

/** Компенсация за задержку доставки: берём сильнейший подходящий тир (delay > N дней). */
export function calcDeliveryCompensation(
  p: Policy,
  delayDays: number,
  orderSum: number,
): { pct: number; amount: number } | null {
  let pct = 0;
  for (const t of p.delivery_compensation) {
    if (delayDays > t.delay_days_gt) pct = Math.max(pct, t.pct_of_sum);
  }
  if (pct === 0) return null;
  return { pct, amount: Math.round((orderSum * pct) / 100) };
}

export interface SlaInfo {
  isWorkTime: boolean;
  hours: number;
  label: string;
}

/** «Честный срок» связи с менеджером: учитывает рабочие часы и выходные (Ст. 6, 8). */
export function getManagerSla(p: Policy, now = new Date()): SlaInfo {
  const { from, to, days } = p.manager_sla.work_hours;
  const day = ((now.getDay() + 6) % 7) + 1; // 1=пн … 7=вс
  const [fh, fm] = from.split(":").map(Number);
  const [th, tm] = to.split(":").map(Number);
  const minutes = now.getHours() * 60 + now.getMinutes();
  const isWorkTime = days.includes(day) && minutes >= fh * 60 + fm && minutes < th * 60 + tm;
  const hours = isWorkTime ? p.manager_sla.response_hours : p.manager_sla.off_hours_response_hours;
  const label = isWorkTime
    ? `≈${hours} ч (рабочее время)`
    : `≈${hours} ч, в начале рабочего дня (сейчас нерабочее время)`;
  return { isWorkTime, hours, label };
}
