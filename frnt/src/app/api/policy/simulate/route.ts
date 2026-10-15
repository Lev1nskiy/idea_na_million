import { proxySite } from "@/server/backend";
import { mockAdminGuard } from "@/server/users";
import { calcAllowedDiscount, calcDeliveryCompensation, getManagerSla } from "@/lib/policy";
import { store } from "@/server/store";

export const dynamic = "force-dynamic";

/** Симулятор матрицы на живых значениях из БД (антихрупкость демо: считается той же функцией, что и в движке). */
export async function POST(req: Request) {
  const raw = await req.text();
  const body = JSON.parse(raw || "{}") as { sum?: number; qty?: number; segment?: "new" | "std" | "loyal" | "corp"; isBundle?: boolean; delayDays?: number; orderSum?: number };

  // На бэкенде расчёт идёт теми же функциями, что и в пайплайне (core/policy.py).
  const proxied = await proxySite("/policy/simulate", { method: "POST", body: raw });
  if (proxied) return proxied;

  const denied = await mockAdminGuard(); // симулятор раскрывает матрицу — тоже админская зона
  if (denied) return denied;

  const p = store().policy;
  const discount = calcAllowedDiscount(p, {
    qty: body.qty ?? 1,
    orderSum: body.sum ?? 0,
    segment: body.segment ?? "std",
    isBundle: body.isBundle ?? false,
  });
  return Response.json({
    policyVersion: p.version,
    discount,
    discountAmount: Math.round(((body.sum ?? 0) * discount.pct) / 100),
    compensation: calcDeliveryCompensation(p, body.delayDays ?? 0, body.orderSum ?? body.sum ?? 0),
    sla: getManagerSla(p),
  });
}
