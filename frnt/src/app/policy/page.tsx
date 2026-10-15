"use client";

import { useEffect, useState } from "react";
import {
  AlarmClock, BadgePercent, CircleCheckBig, History, LifeBuoy, Plus, Save, SlidersHorizontal, Truck, X, Zap,
} from "lucide-react";
import { api, type SimulateResult } from "@/lib/api";
import { ShieldAlert } from "lucide-react";
import { fmtClock, fmtDate, fmtRub } from "@/lib/format";
import { Btn, Chip, Field, Glass, PageHeader, cx, inputCls } from "@/components/ui";
import type { Policy, PolicyVersionMeta } from "@/lib/types";

type Editable = Omit<Policy, "version" | "author" | "updatedAt">;

const DAY_LABELS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];

export default function PolicyPage() {
  const [form, setForm] = useState<Editable | null>(null);
  const [version, setVersion] = useState(0);
  const [history, setHistory] = useState<PolicyVersionMeta[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [sim, setSim] = useState({ sum: 480_000, qty: 12, segment: "loyal", isBundle: true, delayDays: 3 });
  const [simRes, setSimRes] = useState<SimulateResult | null>(null);

  const load = async () => {
    const [{ policy }, { history }] = await Promise.all([api.policy(), api.policyHistory()]);
    const { version, ...rest } = policy;
    setForm(structuredClone(rest));
    setVersion(version);
    setHistory(history);
    return version;
  };
  // Редактор политики открыт только администратору: менеджер видит понятное объяснение,
  // а не пустой экран или ошибку 403.
  useEffect(() => {
    let alive = true;
    api.me()
      .then(({ user }) => {
        if (!alive) return;
        const isAdmin = user.role === "admin";
        setAllowed(isAdmin);
        if (isAdmin) void load();
      })
      .catch(() => { if (alive) setAllowed(false); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (allowed !== true) return;
    const t = setTimeout(() => api.simulate(sim).then(setSimRes).catch(() => null), 250);
    return () => clearTimeout(t);
  }, [sim, allowed]);

  if (allowed === false) {
    return (
      <div className="mx-auto max-w-3xl pt-10">
        <Glass className="p-6">
          <div className="flex items-center gap-2 text-[13px] font-medium text-danger">
            <ShieldAlert size={15} /> Редактор политики доступен только администратору
          </div>
          <p className="mt-2 text-[12px] leading-relaxed text-dim">
            Policy Matrix задаёт скидки, компенсации, гарантию и SLA — их меняет администратор.
            Как менеджер вы видите решения и можете переопределить любое из них в дашборде.
          </p>
        </Glass>
      </div>
    );
  }

  if (allowed === null || !form) {
    return <div className="p-8 text-[13px] text-dim">Загрузка матрицы…</div>;
  }

  const set = <K extends keyof Editable>(k: K, v: Editable[K]) => setForm({ ...form, [k]: v });

  const save = async () => {
    setSaving(true);
    try {
      const res = await api.putPolicy(form);
      setSaved(res.summary);
      await load();
      setTimeout(() => setSaved(null), 6000);
    } finally { setSaving(false); }
  };

  const quickJury = (pct: number) => {
    set("discounts", { ...form.discounts, max_pct: pct });
  };

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Policy Management"
        sub="Версионируемая матрица: скидки, гарантия, компенсации, remedy, SLA менеджера и пороги уверенности. ИИ читает актуальную версию на каждый запрос — кэша нет."
        right={
          <>
            <Chip tone="acc" className="mono">v{version}</Chip>
            <Btn onClick={() => void save()} disabled={saving}>
              <Save size={14} /> {saving ? "Сохранение…" : "Сохранить новую версию"}
            </Btn>
          </>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-mint/30 bg-mint/[0.06] px-4 py-3 text-[12.5px] text-mint">
        <Zap size={15} />
        Изменения вступают в силу немедленно — следующий запрос в чате использует новые значения.
        <span className="ml-auto flex items-center gap-2 text-[11px] text-dim">
          Демо для жюри:
          <button onClick={() => quickJury(10)} className="rounded-md border border-amber/40 bg-amber/10 px-2 py-1 text-amber transition hover:bg-amber/20">max_pct → 10%</button>
          <button onClick={() => quickJury(15)} className="rounded-md border border-edge px-2 py-1 text-dim transition hover:text-txt">вернуть 15%</button>
          затем сценарий «Скидка» в чате
        </span>
      </div>

      {saved && (
        <div className="mb-4 flex items-center gap-2 rounded-xl border border-acc/40 bg-acc/10 px-4 py-2.5 text-[12px] text-txt">
          <CircleCheckBig size={14} className="text-mint" /> Версия v{version} опубликована: {saved}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_330px]">
        <div className="space-y-4">
          {/* скидки */}
          <Glass className="p-5">
            <SectionTitle icon={<BadgePercent size={15} className="text-mint" />} title="Скидки" />
            <div className="grid grid-cols-3 gap-3">
              <Field label="max_pct, %" hint="глобальный потолок">
                <input type="number" className={inputCls} value={form.discounts.max_pct}
                  onChange={(e) => set("discounts", { ...form.discounts, max_pct: num(e) })} />
              </Field>
              <Field label="bundle_max_pct, %" hint="комплекты">
                <input type="number" className={inputCls} value={form.discounts.bundle_max_pct}
                  onChange={(e) => set("discounts", { ...form.discounts, bundle_max_pct: num(e) })} />
              </Field>
              <Field label="loyal_bonus_pct, %" hint="постоянным">
                <input type="number" className={inputCls} value={form.discounts.loyal_bonus_pct}
                  onChange={(e) => set("discounts", { ...form.discounts, loyal_bonus_pct: num(e) })} />
              </Field>
            </div>
            <div className="mt-4 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-[11px] uppercase tracking-wider text-dim">Тиры по сумме заказа</span>
                <button onClick={() => set("discounts", { ...form.discounts, tiers: [...form.discounts.tiers, { min_sum: 100_000, pct: 7 }] })}
                  className="inline-flex items-center gap-1 text-[11px] text-acc2 hover:text-txt"><Plus size={12} />тир</button>
              </div>
              {form.discounts.tiers.map((t, i) => (
                <div key={i} className="flex items-center gap-2">
                  <span className="text-[11px] text-dim">от</span>
                  <input type="number" className={cx(inputCls, "max-w-[140px]")} value={t.min_sum}
                    onChange={(e) => updateTier(i, { ...t, min_sum: num(e) })} />
                  <span className="text-[11px] text-dim">₽ →</span>
                  <input type="number" className={cx(inputCls, "max-w-[90px]")} value={t.pct}
                    onChange={(e) => updateTier(i, { ...t, pct: num(e) })} />
                  <span className="text-[11px] text-dim">%</span>
                  <button onClick={() => updateTier(i, null)} className="ml-auto text-dim hover:text-danger"><X size={14} /></button>
                </div>
              ))}
            </div>
          </Glass>

          {/* гарантия + доставка */}
          <div className="grid gap-4 md:grid-cols-2">
            <Glass className="p-5">
              <SectionTitle icon={<LifeBuoy size={15} className="text-amber" />} title="Гарантия" />
              <div className="grid grid-cols-1 gap-3">
                <Field label="Замена, часов" hint="G: «замена за 24 ч без вопросов»">
                  <input type="number" className={inputCls} value={form.warranty.replace_hours}
                    onChange={(e) => set("warranty", { ...form.warranty, replace_hours: num(e) })} />
                </Field>
                <label className="flex items-center gap-2 text-[12px] text-dim">
                  <input type="checkbox" checked={form.warranty.requires_confirmed_order}
                    onChange={(e) => set("warranty", { ...form.warranty, requires_confirmed_order: e.target.checked })}
                    className="size-4 accent-[#7c6cff]" />
                  требуется подтверждённый заказ (иначе — сбор данных, не отказ)
                </label>
              </div>
            </Glass>
            <Glass className="p-5">
              <SectionTitle icon={<Truck size={15} className="text-acc2" />} title="Компенсации доставки" />
              <div className="space-y-2">
                {form.delivery_compensation.map((c, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <span className="text-[11px] text-dim">задержка больше</span>
                    <input type="number" className={cx(inputCls, "max-w-[70px]")} value={c.delay_days_gt}
                      onChange={(e) => updateComp(i, { ...c, delay_days_gt: num(e) })} />
                    <span className="text-[11px] text-dim">дн. →</span>
                    <input type="number" className={cx(inputCls, "max-w-[70px]")} value={c.pct_of_sum}
                      onChange={(e) => updateComp(i, { ...c, pct_of_sum: num(e) })} />
                    <span className="text-[11px] text-dim">% от суммы</span>
                    <button onClick={() => updateComp(i, null)} className="ml-auto text-dim hover:text-danger"><X size={14} /></button>
                  </div>
                ))}
                <button onClick={() => set("delivery_compensation", [...form.delivery_compensation, { delay_days_gt: 7, pct_of_sum: 15 }])}
                  className="inline-flex items-center gap-1 text-[11px] text-acc2 hover:text-txt"><Plus size={12} />тир компенсации</button>
              </div>
              <div className="mt-4">
                <Field label="Remedy-опции (через запятую)" hint="показываются в спорах как ориентир, без обязательств">
                  <input className={inputCls} value={form.remedy_options.join(", ")}
                    onChange={(e) => set("remedy_options", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
                </Field>
              </div>
            </Glass>
          </div>

          {/* SLA + пороги */}
          <div className="grid gap-4 md:grid-cols-2">
            <Glass className="p-5">
              <SectionTitle icon={<AlarmClock size={15} className="text-acc" />} title="SLA менеджера (честный срок)" />
              <div className="grid grid-cols-2 gap-3">
                <Field label="В рабочее время, ч">
                  <input type="number" className={inputCls} value={form.manager_sla.response_hours}
                    onChange={(e) => set("manager_sla", { ...form.manager_sla, response_hours: num(e) })} />
                </Field>
                <Field label="Вне часов, ч">
                  <input type="number" className={inputCls} value={form.manager_sla.off_hours_response_hours}
                    onChange={(e) => set("manager_sla", { ...form.manager_sla, off_hours_response_hours: num(e) })} />
                </Field>
                <Field label="Раб. день с">
                  <input type="time" className={inputCls} value={form.manager_sla.work_hours.from}
                    onChange={(e) => set("manager_sla", { ...form.manager_sla, work_hours: { ...form.manager_sla.work_hours, from: e.target.value } })} />
                </Field>
                <Field label="до">
                  <input type="time" className={inputCls} value={form.manager_sla.work_hours.to}
                    onChange={(e) => set("manager_sla", { ...form.manager_sla, work_hours: { ...form.manager_sla.work_hours, to: e.target.value } })} />
                </Field>
              </div>
              <div className="mt-3 flex gap-1.5">
                {DAY_LABELS.map((d, i) => {
                  const on = form.manager_sla.work_hours.days.includes(i + 1);
                  return (
                    <button key={d}
                      onClick={() => {
                        const days = on ? form.manager_sla.work_hours.days.filter((x) => x !== i + 1) : [...form.manager_sla.work_hours.days, i + 1].sort();
                        set("manager_sla", { ...form.manager_sla, work_hours: { ...form.manager_sla.work_hours, days } });
                      }}
                      className={cx("w-9 rounded-lg border px-1 py-1.5 text-[11px] transition",
                        on ? "border-acc/50 bg-acc/15 text-txt" : "border-edge text-dim hover:text-txt")}>
                      {d}
                    </button>
                  );
                })}
              </div>
            </Glass>
            <Glass className="p-5">
              <SectionTitle icon={<SlidersHorizontal size={15} className="text-fuchsia-300" />} title="Пороги решений" />
              <div className="grid grid-cols-2 gap-3">
                <Field label="min_confidence" hint="ниже → clarify, а не I">
                  <input type="number" step="0.05" min="0" max="1" className={inputCls} value={form.thresholds.min_confidence}
                    onChange={(e) => set("thresholds", { ...form.thresholds, min_confidence: Number(e.target.value) })} />
                </Field>
                <Field label="discount_push_limit" hint="настаивание → I">
                  <input type="number" className={inputCls} value={form.thresholds.discount_push_limit}
                    onChange={(e) => set("thresholds", { ...form.thresholds, discount_push_limit: num(e) })} />
                </Field>
                <Field label="negative_sentiment" hint="ниже → I">
                  <input type="number" step="0.1" min="-1" max="0" className={inputCls} value={form.thresholds.negative_sentiment}
                    onChange={(e) => set("thresholds", { ...form.thresholds, negative_sentiment: Number(e.target.value) })} />
                </Field>
                <Field label="hold_ping, мин">
                  <input type="number" className={inputCls} value={form.thresholds.hold_ping_minutes}
                    onChange={(e) => set("thresholds", { ...form.thresholds, hold_ping_minutes: num(e) })} />
                </Field>
                <Field label="reminder, мин" hint="дубли уведомлений до takeover">
                  <input type="number" className={inputCls} value={form.thresholds.reminder_minutes}
                    onChange={(e) => set("thresholds", { ...form.thresholds, reminder_minutes: num(e) })} />
                </Field>
              </div>
            </Glass>
          </div>
        </div>

        {/* правая колонка: симулятор + история версий */}
        <div className="space-y-4">
          <Glass className="edge-glow p-5">
            <SectionTitle icon={<Zap size={15} className="text-mint" />} title="Симулятор на живых данных" />
            <div className="grid grid-cols-2 gap-3">
              <Field label="Сумма заказа, ₽">
                <input type="number" className={inputCls} value={sim.sum} onChange={(e) => setSim({ ...sim, sum: num(e) })} />
              </Field>
              <Field label="Кол-во, шт">
                <input type="number" className={inputCls} value={sim.qty} onChange={(e) => setSim({ ...sim, qty: num(e) })} />
              </Field>
              <Field label="Сегмент">
                <select className={inputCls} value={sim.segment} onChange={(e) => setSim({ ...sim, segment: e.target.value })}>
                  <option value="new">новый</option><option value="std">стандарт</option>
                  <option value="loyal">постоянный</option><option value="corp">корпоративный</option>
                </select>
              </Field>
              <Field label="Задержка, дней">
                <input type="number" className={inputCls} value={sim.delayDays} onChange={(e) => setSim({ ...sim, delayDays: num(e) })} />
              </Field>
            </div>
            <label className="mt-3 flex items-center gap-2 text-[12px] text-dim">
              <input type="checkbox" checked={sim.isBundle} onChange={(e) => setSim({ ...sim, isBundle: e.target.checked })} className="size-4 accent-[#7c6cff]" />
              комплект (bundle)
            </label>
            {simRes && (
              <div className="mt-4 space-y-2 rounded-xl border border-edge bg-white/[0.02] p-3.5 text-[12px]">
                <SimRow k="Допустимая скидка" v={`${simRes.discount.pct}% → −${fmtRub(simRes.discountAmount)}`} accent />
                <div className="text-[10px] leading-snug text-dim">{simRes.discount.reason} · Policy v{simRes.policyVersion}</div>
                <SimRow k="Компенсация доставки" v={simRes.compensation ? `${simRes.compensation.pct}% = ${fmtRub(simRes.compensation.amount)}` : "не положена"} />
                <SimRow k="SLA менеджера сейчас" v={simRes.sla.label} />
              </div>
            )}
          </Glass>

          <Glass className="p-5">
            <SectionTitle icon={<History size={15} className="text-acc" />} title="История версий" />
            <div className="space-y-3">
              {history.map((h, i) => (
                <div key={h.version} className="relative border-l border-edge pl-4">
                  <span className={cx("absolute -left-[5px] top-1 size-2.5 rounded-full border-2 border-ink", i === 0 ? "bg-mint" : "bg-dim/50")} />
                  <div className="flex items-center gap-2">
                    <span className="mono text-[11.5px] font-semibold text-txt">v{h.version}</span>
                    {i === 0 && <Chip tone="mint" className="px-1.5 py-0 text-[9px]">текущая</Chip>}
                    <span className="mono ml-auto text-[9.5px] text-dim">{fmtDate(h.at)} {fmtClock(h.at)}</span>
                  </div>
                  <div className="mt-0.5 text-[11px] text-dim">{h.author}</div>
                  <div className="mt-1 text-[11px] leading-snug text-txt/80">{h.summary}</div>
                </div>
              ))}
            </div>
          </Glass>
        </div>
      </div>
    </div>
  );

  function updateTier(i: number, v: { min_sum: number; pct: number } | null) {
    setForm((prev) => {
      if (!prev) return prev;
      const tiers = [...prev.discounts.tiers];
      if (v === null) tiers.splice(i, 1); else tiers[i] = v;
      return { ...prev, discounts: { ...prev.discounts, tiers } };
    });
  }
  function updateComp(i: number, v: { delay_days_gt: number; pct_of_sum: number } | null) {
    setForm((prev) => {
      if (!prev) return prev;
      const arr = [...prev.delivery_compensation];
      if (v === null) arr.splice(i, 1); else arr[i] = v;
      return { ...prev, delivery_compensation: arr };
    });
  }
}

function SectionTitle({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <div className="mb-4 flex items-center gap-2.5">
      {icon}
      <h3 className="font-display text-[13px] font-semibold tracking-wide">{title}</h3>
    </div>
  );
}

function SimRow({ k, v, accent }: { k: string; v: string; accent?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-dim">{k}</span>
      <span className={cx("mono text-right", accent && "text-mint font-semibold")}>{v}</span>
    </div>
  );
}

function num(e: React.ChangeEvent<HTMLInputElement>): number {
  return Number(e.target.value.replace(/\s/g, "")) || 0;
}
