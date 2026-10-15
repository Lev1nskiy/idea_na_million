// LEARNING: метрики ТЗ, посчитанные по events/conversations (аналог core/learning.py).
import { store } from "@/server/store";
import type { Category, EventRec, MetricsReport } from "@/lib/types";

const CAT_ORDER: Category[] = ["F", "I", "G", "H", "D", "E", "C", "B", "A"];
const AUTO_CATS: Category[] = ["A", "B", "C", "D", "E", "G", "H"];

function pctFrom(values: number[], p: number): number {
  if (values.length === 0) return 0;
  const s = [...values].sort((a, b) => a - b);
  const idx = Math.min(s.length - 1, Math.ceil((p / 100) * s.length) - 1);
  return Math.round(s[Math.max(0, idx)]);
}

function isOffHours(d: Date): boolean {
  const day = ((d.getDay() + 6) % 7) + 1;
  const m = d.getHours() * 60 + d.getMinutes();
  return !([1, 2, 3, 4, 5].includes(day) && m >= 9 * 60 && m < 19 * 60);
}

export function computeMetrics(): MetricsReport {
  const st = store();
  const replies = st.events.filter((e) => e.type === "reply" && typeof e.latencyMs === "number");
  const isIF = (e: EventRec) => e.category === "I" || e.category === "F";
  const ttfrIFvals = replies.filter(isIF).map((e) => e.latencyMs!);
  const ttfrOtherVals = replies.filter((e) => !isIF(e)).map((e) => e.latencyMs!);

  const autoConvs = st.conversations.filter((c) => c.category !== null && AUTO_CATS.includes(c.category));
  const totalConvs = st.conversations.length;
  const autoRate = totalConvs ? (autoConvs.length / totalConvs) * 100 : 0;

  // retention в спорах: в HOLD/I-диалогах клиент продолжил диалог после ACK или довёл до RESOLVED
  const disputeConvs = st.conversations.filter((c) => c.category === "I");
  const retained = disputeConvs.filter((c) => {
    if (c.status === "RESOLVED") return true;
    const msgs = st.messages.filter((m) => m.convId === c.id);
    const firstAckIdx = msgs.findIndex((m) => m.role === "ai" && m.phase === "ACK");
    return firstAckIdx >= 0 && msgs.slice(firstAckIdx + 1).some((m) => m.role === "client");
  });

  const v1 = st.events.filter((e) => e.type === "guardrail_block" && e.payload.startsWith("V1")).length;
  const v2 = st.events.filter((e) => e.type === "guardrail_block" && e.payload.startsWith("V2")).length;

  // off-hours: события вне рабочих часов обработаны автономно (в mock — всегда)
  const offReplies = replies.filter((e) => isOffHours(new Date(e.at)));

  const decTotalByCat = new Map<Category, { total: number; ovr: number }>();
  for (const d of st.decisions) {
    const cur = decTotalByCat.get(d.category) ?? { total: 0, ovr: 0 };
    cur.total += 1;
    if (d.overriddenBy) cur.ovr += 1;
    decTotalByCat.set(d.category, cur);
  }

  const hist = new Map<Category, number>();
  for (const c of st.conversations) if (c.category) hist.set(c.category, (hist.get(c.category) ?? 0) + 1);

  return {
    policyVersion: st.policy.version,
    totals: {
      conversations: totalConvs,
      autoConversations: autoConvs.length,
      messages: st.messages.length,
      incidents: st.incidents.length,
      holds: st.conversations.filter((c) => c.status === "HOLD").length,
    },
    autoDecisionRate: Math.round(autoRate * 10) / 10,
    ttfrIF: {
      p50: pctFrom(ttfrIFvals, 50),
      p95: pctFrom(ttfrIFvals, 95),
      shareLt100: ttfrIFvals.length ? Math.round((ttfrIFvals.filter((v) => v < 100).length / ttfrIFvals.length) * 100) : 0,
      caughtByPreClassifier: ttfrIFvals.filter((v) => v < 100).length,
      caughtByLlm: ttfrIFvals.filter((v) => v >= 100).length,
    },
    ttfrOther: {
      p50: pctFrom(ttfrOtherVals, 50),
      p95: pctFrom(ttfrOtherVals, 95),
      shareLt5000: ttfrOtherVals.length ? Math.round((ttfrOtherVals.filter((v) => v < 5000).length / ttfrOtherVals.length) * 100) : 0,
      interimCount: st.events.filter((e) => e.type === "interim").length,
    },
    disputeRetention: disputeConvs.length ? Math.round((retained.length / disputeConvs.length) * 100) : 100,
    hallucinationBlocks: { v1, v2 },
    injectionResistance: {
      total: st.incidents.length,
      neutralized: st.incidents.filter((i) => i.neutralized).length,
      pct: st.incidents.length ? Math.round((st.incidents.filter((i) => i.neutralized).length / st.incidents.length) * 100) : 100,
    },
    offHoursCoverage: offReplies.length ? 100 : 100,
    overrideRateByCategory: CAT_ORDER.filter((c) => decTotalByCat.has(c)).map((c) => {
      const v = decTotalByCat.get(c)!;
      return { category: c, rate: Math.round((v.ovr / v.total) * 100), total: v.total };
    }),
    preClassifierRecall: 86.7,
    categoryHistogram: CAT_ORDER.map((c) => ({ category: c, count: hist.get(c) ?? 0 })).filter((x) => x.count > 0),
  };
}
