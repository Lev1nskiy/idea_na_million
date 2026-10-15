import { calcAllowedDiscount, calcDeliveryCompensation, getManagerSla } from "@/lib/policy";
import { store } from "@/server/store";

export const dynamic = "force-dynamic";

/** Симулятор матрицы на живых значениях из БД (антихрупкость демо: считается той же функцией, что и в движке). */
export async function POST(req: Request) {
  const body = (await req.json()) as { sum?: number; qty?: number; segment?: "new" | "std" | "loyal" | "corp"; isBundle?: boolean; delayDays?: number; orderSum?: number };
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
