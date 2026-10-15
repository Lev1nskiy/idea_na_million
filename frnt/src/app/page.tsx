"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import {
  ArrowRight, BrainCircuit, ClipboardCheck, Eye, FileText, Gauge, Inbox, Route,
  ShieldCheck, SlidersHorizontal, Sparkles, Wrench,
} from "lucide-react";
import { usePolling } from "@/lib/hooks";
import { api, type MetricsDto } from "@/lib/api";
import { Btn, Chip, Kpi } from "@/components/ui";
import { useState } from "react";
import { fmtMs } from "@/lib/format";

const STAGES = [
  { code: "01", name: "PERCEPTION", icon: Eye, lines: ["нормализация, таймер 3.5 с", "лексический pre-classifier <100 мс", "injection regex → legit_text"] },
  { code: "02", name: "UNDERSTANDING", icon: BrainCircuit, lines: ["LLM → структурный JSON", "intent · entities · sentiment", "confidence, temperature 0"] },
  { code: "03", name: "DECISION", icon: Route, lines: ["приоритет F>I>G/H>D>E>C>B>A", "граница C/I по матрице", "confidence 0.7 → clarify"] },
  { code: "04", name: "ACTION", icon: Wrench, lines: ["tools: каталог, скидка, гарантия", "guardrails V1–V7 на выходе", "обновление карточки сделки"] },
  { code: "05", name: "LEARNING", icon: ClipboardCheck, lines: ["events·логи (PII замаскированы)", "метрики TTFR / auto-rate", "подсказки по политике"] },
];

const CHARTER: [string, string, string][] = [
  ["Ст. 1", "ИИ действует автономно в рамках Устава", "decision.py — автономные маршруты A–H"],
  ["Ст. 2", "Числа и скидки — только из инструментов и политики", "tools.py + guardrails V1/V2 (whitelist чисел)"],
  ["Ст. 3", "Инъекция: скрипт-ответ, инцидент, диалог не блокируется", "perception.py + f_script + security_incidents"],
  ["Ст. 4", "Чужие ПДн не попадают в контекст, логи и ответы", "privacy.py (mask_pii) + client-scoped retrieval"],
  ["Ст. 5", "Менеджер переопределяет любое авто-решение", "decisions + /api/decisions/{id}/override"],
  ["Ст. 6", "Признание проблемы без признания вины, честный SLA", "templates.py + get_manager_sla + валидатор V3"],
  ["Ст. 7", "Тон: эмпатично и профессионально", "prompts/generation + валидатор V6 (Smooth)"],
  ["Ст. 8", "Handoff: саммари читается за 30 секунд", "handoff.py — build_summary (структура)"],
  ["Ст. 9", "Клиент не остаётся в тишине до takeover", "hold-loop: ping + дубли уведомлений каждые N мин"],
];

export default function HomePage() {
  const [m, setM] = useState<MetricsDto | null>(null);
  usePolling(async () => {
    try { setM(await api.metrics()); } catch { /* офлайн */ }
  }, 5000);

  return (
    <div className="relative">
      <div className="bg-grid pointer-events-none absolute inset-x-0 top-0 h-[420px]" />

      {/* HERO */}
      <section className="relative mx-auto max-w-6xl pt-8 md:pt-14">
        <div className="flex flex-wrap items-center gap-2">
          <Chip tone="acc"><Sparkles size={11} /> Хакатон · MVP v5 · Policy Matrix</Chip>
          <Chip tone="mint"><ShieldCheck size={11} /> Устав исполняется кодом, а не промптом</Chip>
        </div>
        <motion.h1
          initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }}
          className="font-display mt-6 max-w-4xl text-[30px] font-semibold leading-[1.12] tracking-tight md:text-[52px]"
        >
          ИИ, который <span className="grad-text">продаёт по политике</span> и гасит конфликты за&nbsp;миллисекунды.
        </motion.h1>
        <p className="mt-4 max-w-2xl text-[14px] leading-relaxed text-dim">
          Пятислойный пайплайн: восприятие → понимание → решение → действие → обучение.
          LLM отвечает только за язык — цифры, скидки и SLA рождаются в детерминированном коде
          из версионируемой Policy Matrix.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <Link href="/chat"><Btn className="px-5 py-2.5 text-[13px]">Чат клиента <ArrowRight size={14} /></Btn></Link>
          <Link href="/dashboard"><Btn tone="ghost" className="px-5 py-2.5 text-[13px]"><Inbox size={14} /> Дашборд менеджера</Btn></Link>
          <Link href="/policy"><Btn tone="ghost" className="px-5 py-2.5 text-[13px]"><SlidersHorizontal size={14} /> Policy Admin</Btn></Link>
        </div>

        {/* живые KPI */}
        <div className="mt-8 grid grid-cols-2 gap-3 md:grid-cols-4">
          <Kpi label="Auto-decision rate" value={m ? `${m.autoDecisionRate}%` : "—"} sub="диалоги A–H / все диалоги" tone="mint" />
          <Kpi label="TTFR — споры и инъекции" value={m ? fmtMs(m.ttfrIF.p50) : "—"} sub={m ? `p95 ${fmtMs(m.ttfrIF.p95)} · <100 мс: ${m.ttfrIF.shareLt100}%` : "pre-classifier + шаблон"} tone="sky" />
          <Kpi label="Injection resistance" value={m ? `${m.injectionResistance.pct}%` : "—"} sub={m ? `инцидентов: ${m.injectionResistance.total}, нейтрализовано: ${m.injectionResistance.neutralized}` : ""} tone="acc" />
          <Kpi label="Сейчас в HOLD" value={m?.totals.holds ?? "—"} sub="дубли уведомлений до takeover" tone="danger" />
        </div>
      </section>

      {/* PIPELINE */}
      <section className="relative mx-auto mt-14 max-w-6xl">
        <div className="mb-5 flex items-center gap-3">
          <Gauge size={16} className="text-acc" />
          <h2 className="font-display text-[15px] font-semibold tracking-wide">Пайплайн соответствует ТЗ 1:1</h2>
          <span className="text-[11px] text-dim">— каркас 5 слоёв + контур политики</span>
        </div>
        <div className="grid gap-3 lg:grid-cols-5">
          {STAGES.map((s, i) => {
            const Icon = s.icon;
            return (
              <div key={s.code} className="relative">
                <motion.div
                  initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.08 * i, duration: 0.4 }}
                  className="glass glass-hover h-full p-4"
                >
                  <div className="flex items-center justify-between">
                    <span className="mono text-[11px] text-dim">{s.code}</span>
                    <Icon size={16} className="text-acc2" />
                  </div>
                  <div className="mono mt-2 text-[12px] font-semibold tracking-wider text-txt">{s.name}</div>
                  <ul className="mt-2.5 space-y-1.5">
                    {s.lines.map((l) => (
                      <li key={l} className="flex gap-1.5 text-[10.5px] leading-snug text-dim">
                        <span className="mt-[5px] size-1 shrink-0 rounded-full bg-acc/70" />{l}
                      </li>
                    ))}
                  </ul>
                </motion.div>
                {i < STAGES.length - 1 && (
                  <div className="flowline absolute -right-3 top-1/2 hidden h-px w-3 lg:block" />
                )}
              </div>
            );
          })}
        </div>
        <div className="edge-glow glass mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <div className="flex items-center gap-2.5">
            <SlidersHorizontal size={15} className="text-mint" />
            <span className="mono text-[12px] font-semibold tracking-wider">POLICY MANAGEMENT</span>
          </div>
          <div className="text-[11px] text-dim">скидки · warranty · компенсации доставки · remedy · SLA менеджера · пороги</div>
          <div className="ml-auto flex items-center gap-3">
            <Chip tone="mint">без кэша — читается на каждый запрос</Chip>
            <Chip tone="acc">версия v{m?.policyVersion ?? "…"}</Chip>
          </div>
        </div>
      </section>

      {/* УСТАВ → КОД */}
      <section className="relative mx-auto mt-14 max-w-6xl">
        <div className="mb-5 flex items-center gap-3">
          <FileText size={16} className="text-acc" />
          <h2 className="font-display text-[15px] font-semibold tracking-wide">Устав → исполняемый механизм</h2>
        </div>
        <div className="glass overflow-hidden">
          {CHARTER.map(([art, text, mech], i) => (
            <div key={art} className={`grid gap-1 px-4 py-3 md:grid-cols-[64px_1fr_1fr] md:items-center md:gap-4 ${i % 2 ? "bg-white/[0.02]" : ""}`}>
              <span className="mono text-[11px] font-semibold text-acc2">{art}</span>
              <span className="text-[12.5px] text-txt/90">{text}</span>
              <span className="mono text-[10.5px] text-dim">{mech}</span>
            </div>
          ))}
        </div>
        <p className="mt-4 text-[11.5px] text-dim">
          Backend-контракты соответствуют плану: REST-эндпоинты <span className="mono text-acc2">/api/messages · /api/conversations · /api/policy · /api/metrics · /api/security/incidents</span>.
          Каналы (web-чат, Telegram) — тонкие адаптеры над одним оркестратором.
        </p>
      </section>
    </div>
  );
}
