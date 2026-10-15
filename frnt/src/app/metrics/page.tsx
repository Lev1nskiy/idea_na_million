"use client";

import { useState } from "react";
import {
  Activity, BellRing, Gauge, LockKeyhole, Radar, ShieldCheck, ShieldOff, Swords, Timer, Undo2, ZapOff,
} from "lucide-react";
import { api, type MetricsDto } from "@/lib/api";
import { usePolling } from "@/lib/hooks";
import { fmtClock, fmtDate, fmtMs } from "@/lib/format";
import { CategoryChip, Chip, Glass, HBar, Kpi, PageHeader, Radial, cx } from "@/components/ui";
import type { Category, SecurityIncident } from "@/lib/types";

const CAT_TONE: Record<Category, "acc" | "mint" | "danger" | "amber" | "sky" | "violet"> = {
  A: "sky", B: "acc", C: "mint", D: "violet", E: "sky", F: "danger", G: "amber", H: "amber", I: "danger",
};

export default function MetricsPage() {
  const [m, setM] = useState<MetricsDto | null>(null);
  const [incidents, setIncidents] = useState<SecurityIncident[]>([]);
  const [reason, setReason] = useState<Record<string, string>>({});

  usePolling(async () => {
    try {
      const [mx, inc] = await Promise.all([api.metrics(), api.incidents()]);
      setM(mx);
      setIncidents(inc.incidents);
    } catch { /* офлайн */ }
  }, 4000);

  if (!m) return <div className="p-8 text-[13px] text-dim">Считаем метрики…</div>;

  const maxHist = Math.max(...m.categoryHistogram.map((h) => h.count), 1);

  return (
    <div>
      <PageHeader
        title="Безопасность и метрики"
        sub="Метрики LEARNING-слоя: формулы из ТЗ, события в журнале замаскированы mask_pii (Ст. 4). Обновление — каждые 4 секунды."
        right={<Chip tone="acc" className="mono">Policy v{m.policyVersion}</Chip>}
      />

      {/* верхняя линейка KPI */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <Glass className="col-span-2 flex items-center gap-4 p-4 md:col-span-4 xl:col-span-2">
          <Radial value={m.autoDecisionRate} label="auto-decision" />
          <div>
            <div className="text-[10px] uppercase tracking-[0.18em] text-dim">Auto-decision rate</div>
            <div className="mt-1 text-[12px] leading-snug text-dim">
              <span className="mono text-txt">{m.totals.autoConversations}</span> из <span className="mono text-txt">{m.totals.conversations}</span> диалогов
              закрыты автономно (A–H)
            </div>
            <div className="mono mt-1 text-[10px] text-dim/70">цель ТЗ: &gt;90%</div>
          </div>
        </Glass>
        <Kpi tone="sky" label="TTFR · I/F p50/p95" value={fmtMs(m.ttfrIF.p50)}
          sub={<span>p95 {fmtMs(m.ttfrIF.p95)} · &lt;100 мс: {m.ttfrIF.shareLt100}%<br />pre-classifier: {m.ttfrIF.caughtByPreClassifier} · LLM: {m.ttfrIF.caughtByLlm}</span>} />
        <Kpi tone="sky" label="TTFR · прочие p50/p95" value={fmtMs(m.ttfrOther.p50)}
          sub={<span>p95 {fmtMs(m.ttfrOther.p95)} · &lt;5 с: {m.ttfrOther.shareLt5000}%<br />INTERIM-watchdog: {m.ttfrOther.interimCount}</span>} />
        <Kpi tone="mint" label="Injection resistance" value={`${m.injectionResistance.pct}%`}
          sub={`${m.injectionResistance.neutralized}/${m.injectionResistance.total} нейтрализовано`} />
        <Kpi tone="acc" label="Retention в спорах" value={`${m.disputeRetention}%`} sub="клиент остаётся в диалоге после ACK" />
        <Kpi tone="amber" label="Блокировки чисел" value={m.hallucinationBlocks.v1 + m.hallucinationBlocks.v2}
          sub={<span>V1 whitelist: {m.hallucinationBlocks.v1} · V2 скидка&gt;max: {m.hallucinationBlocks.v2}<br />price hallucination: 0%</span>} />
        <Kpi tone="mint" label="Off-hours coverage" value={`${m.offHoursCoverage}%`} sub={`pre-classifier recall: ${m.preClassifierRecall}%`} />
      </div>

      {/* гистограммы + override */}
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Glass className="p-5">
          <SectionHead icon={<Gauge size={15} className="text-acc" />} title="Категории диалогов" note="категория = максимум по приоритету в диалоге" />
          <div className="space-y-2.5">
            {m.categoryHistogram.map((h) => (
              <HBar key={h.category} label={`${h.category} · ${catLabel(h.category)}`} value={h.count} max={maxHist} tone={CAT_TONE[h.category]} />
            ))}
          </div>
        </Glass>
        <Glass className="p-5">
          <SectionHead icon={<Undo2 size={15} className="text-amber" />} title="Override rate по категориям" note="переопределения менеджера (Ст. 5) — отдельно от формулы auto-rate" />
          <div className="space-y-2.5">
            {m.overrideRateByCategory.length === 0 && <p className="text-[11.5px] text-dim">Ни одного решения не переопределено.</p>}
            {m.overrideRateByCategory.map((o) => (
              <HBar key={o.category} label={`${o.category} · ${o.total} решений`} value={o.rate} max={100} tone="amber" suffix="%" />
            ))}
          </div>
          <div className="mt-4 grid grid-cols-3 gap-2 border-t border-edge pt-3 text-center">
            <MiniStat label="диалогов" value={m.totals.conversations} />
            <MiniStat label="сообщений" value={m.totals.messages} />
            <MiniStat label="инцидентов" value={m.totals.incidents} />
          </div>
        </Glass>
      </div>

      {/* инциденты безопасности */}
      <Glass className="mt-4 overflow-hidden">
        <div className="flex flex-wrap items-center gap-2.5 border-b border-edge px-5 py-3.5">
          <ShieldOff size={15} className="text-danger" />
          <span className="font-display text-[13px] font-semibold">Инциденты безопасности</span>
          <Chip tone="mint"><LockKeyhole size={10} /> только маскированный текст (Ст. 4)</Chip>
          <span className="mono ml-auto text-[10px] text-dim">{incidents.length} записей</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-[11.5px]">
            <thead>
              <tr className="border-b border-edge text-[9.5px] uppercase tracking-wider text-dim">
                <th className="px-5 py-2.5">Время</th>
                <th className="px-3 py-2.5">Клиент</th>
                <th className="px-3 py-2.5">Паттерны</th>
                <th className="px-3 py-2.5">Текст (маскирован)</th>
                <th className="px-3 py-2.5">Hash</th>
                <th className="px-3 py-2.5">Статус</th>
              </tr>
            </thead>
            <tbody>
              {incidents.map((i) => (
                <tr key={i.id} className="border-b border-edge/60 align-top transition hover:bg-white/[0.02]">
                  <td className="mono whitespace-nowrap px-5 py-3 text-dim">{fmtDate(i.at)} {fmtClock(i.at)}</td>
                  <td className="px-3 py-3">{i.clientName}</td>
                  <td className="px-3 py-3">
                    <div className="flex max-w-[180px] flex-wrap gap-1">
                      {i.patterns.map((p) => <span key={p} className="mono rounded bg-danger/10 px-1.5 py-0.5 text-[9px] text-danger">{p}</span>)}
                    </div>
                  </td>
                  <td className="mono max-w-[320px] px-3 py-3 text-dim"><span className="line-clamp-2">{i.rawTextMasked}</span></td>
                  <td className="mono px-3 py-3 text-[10px] text-acc2/80">{i.textHash}</td>
                  <td className="px-3 py-3">
                    <Chip tone="mint" className="px-1.5 py-0 text-[9px]"><ShieldCheck size={10} /> нейтрализована</Chip>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Glass>

      {/* журнал решений + поток событий */}
      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <Glass className="overflow-hidden">
          <div className="flex items-center gap-2.5 border-b border-edge px-5 py-3.5">
            <Swords size={14} className="text-acc" />
            <span className="font-display text-[13px] font-semibold">Журнал решений ИИ</span>
            <span className="mono ml-auto text-[10px] text-dim">override доступен (Ст. 5)</span>
          </div>
          <div className="max-h-[380px] space-y-1.5 overflow-y-auto p-3">
            {m.decisions.map((d) => (
              <div key={d.id} className="rounded-lg border border-edge px-3 py-2">
                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                  <CategoryChip category={d.category} withLabel={false} />
                  <span className="truncate">{d.clientName}</span>
                  <span className="mono text-[9.5px] text-dim">conf {d.confidence.toFixed(2)} · v{d.policyVersion} · {fmtClock(d.createdAt)}</span>
                  {d.overriddenBy && (
                    <Chip tone="amber" className="ml-auto px-1.5 py-0 text-[9px]" >{d.overrideReason}</Chip>
                  )}
                </div>
                {!d.overriddenBy && (
                  <div className="mt-1.5 flex gap-1.5">
                    <input
                      value={reason[d.id] ?? ""}
                      onChange={(e) => setReason({ ...reason, [d.id]: e.target.value })}
                      placeholder="причина override…"
                      className="w-full rounded-md border border-edge bg-white/[0.03] px-2 py-1 text-[10.5px] outline-none focus:border-acc/50"
                    />
                    <button
                      disabled={!(reason[d.id] ?? "").trim()}
                      onClick={() => void api.overrideDecision(d.id, reason[d.id]).then(async () => setM(await api.metrics()))}
                      className="shrink-0 rounded-md border border-amber/40 bg-amber/10 px-2 text-[10px] text-amber transition hover:bg-amber/20 disabled:opacity-30"
                    >
                      override
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </Glass>

        <Glass className="overflow-hidden">
          <div className="flex items-center gap-2.5 border-b border-edge px-5 py-3.5">
            <Activity size={14} className="text-mint" />
            <span className="font-display text-[13px] font-semibold">Поток событий</span>
            <Chip tone="dim" className="ml-auto px-1.5 py-0 text-[9px]"><LockKeyhole size={9} /> PII masked</Chip>
          </div>
          <div className="max-h-[380px] space-y-1 overflow-y-auto p-3">
            {m.events.map((e) => (
              <div key={e.id} className="flex items-baseline gap-2.5 rounded-md px-2 py-1.5 text-[10.5px] transition hover:bg-white/[0.02]">
                <span className="mono shrink-0 text-dim/70">{fmtClock(e.at)}</span>
                <EventIcon type={e.type} />
                <span className="min-w-0 flex-1 truncate text-dim">{e.payload}</span>
                {e.latencyMs != null && (
                  <span className={cx("mono shrink-0 text-[9px]", e.latencyMs < 100 ? "text-mint" : "text-acc2")}>{fmtMs(e.latencyMs)}</span>
                )}
                {e.category && <CategoryChip category={e.category} withLabel={false} />}
              </div>
            ))}
          </div>
        </Glass>
      </div>
    </div>
  );
}

function EventIcon({ type }: { type: string }) {
  const cls = "mt-0.5 size-3 shrink-0";
  if (type === "security") return <ShieldOff className={cx(cls, "text-danger")} />;
  if (type === "guardrail_block") return <ZapOff className={cx(cls, "text-amber")} />;
  if (type === "notify") return <BellRing className={cx(cls, "text-acc2")} />;
  if (type === "takeover") return <Radar className={cx(cls, "text-fuchsia-300")} />;
  return <Timer className={cx(cls, "text-mint")} />;
}

function SectionHead({ icon, title, note }: { icon: React.ReactNode; title: string; note: string }) {
  return (
    <div className="mb-4 flex items-center gap-2.5">
      {icon}
      <span className="font-display text-[13px] font-semibold tracking-wide">{title}</span>
      <span className="text-[10px] text-dim">· {note}</span>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="mono text-lg font-semibold text-txt">{value}</div>
      <div className="text-[9.5px] uppercase tracking-wider text-dim">{label}</div>
    </div>
  );
}

function catLabel(c: Category): string {
  return { A: "справка", B: "заявка", C: "скидка", D: "КП", E: "сложный", F: "инъекция", G: "гарантия", H: "доставка", I: "спор" }[c];
}
