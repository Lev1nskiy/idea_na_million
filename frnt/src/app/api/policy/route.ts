import { logEvent, store } from "@/server/store";
import type { Policy } from "@/lib/types";

export const dynamic = "force-dynamic";

export async function GET() {
  const st = store();
  return Response.json({ policy: st.policy });
}

function summarize(prev: Policy, next: Omit<Policy, "version" | "author" | "updatedAt">): string {
  const diff: string[] = [];
  if (prev.discounts.max_pct !== next.discounts.max_pct) diff.push(`max_pct: ${prev.discounts.max_pct}% → ${next.discounts.max_pct}%`);
  if (JSON.stringify(prev.discounts.tiers) !== JSON.stringify(next.discounts.tiers)) diff.push("обновлены тиры скидок");
  if (prev.warranty.replace_hours !== next.warranty.replace_hours) diff.push(`замена: ${prev.warranty.replace_hours}ч → ${next.warranty.replace_hours}ч`);
  if (JSON.stringify(prev.delivery_compensation) !== JSON.stringify(next.delivery_compensation)) diff.push("обновлены компенсации доставки");
  if (prev.manager_sla.response_hours !== next.manager_sla.response_hours) diff.push(`SLA менеджера: ${next.manager_sla.response_hours}ч`);
  if (prev.thresholds.min_confidence !== next.thresholds.min_confidence) diff.push(`min_confidence → ${next.thresholds.min_confidence}`);
  return diff.join("; ") || "редакция без изменения ключевых значений";
}

/** Обновление создаёт новую версию; вступает в силу немедленно (без кэша, читается на каждый запрос). */
export async function PUT(req: Request) {
  const body = (await req.json()) as { author?: string } & Omit<Policy, "version" | "author" | "updatedAt">;
  const st = store();
  const summary = summarize(st.policy, body);
  const next: Policy = {
    discounts: body.discounts,
    warranty: body.warranty,
    delivery_compensation: body.delivery_compensation,
    remedy_options: body.remedy_options,
    manager_sla: body.manager_sla,
    thresholds: body.thresholds,
    version: st.policy.version + 1,
    author: body.author ?? "Администратор",
    updatedAt: new Date().toISOString(),
  };
  st.policy = next;
  st.policyHistory.unshift({ version: next.version, author: next.author, at: next.updatedAt, summary });
  logEvent("policy_update", `Policy v${next.version}: ${summary} · действует немедленно`);
  return Response.json({ ok: true, policy: next, summary });
}
