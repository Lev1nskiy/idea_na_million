"use client";

import type { ReactNode } from "react";
import type { Category, ConvStatus } from "@/lib/types";

export const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");

export function Glass({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cx("glass", className)}>{children}</div>;
}

export function PageHeader({ title, sub, right }: { title: string; sub?: string; right?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="font-display text-xl font-semibold tracking-tight md:text-2xl">{title}</h1>
        {sub && <p className="mt-1 max-w-2xl text-[13px] leading-relaxed text-dim">{sub}</p>}
      </div>
      {right && <div className="flex items-center gap-2">{right}</div>}
    </div>
  );
}

type Tone = "acc" | "mint" | "danger" | "amber" | "sky" | "dim" | "violet";

const TONES: Record<Tone, string> = {
  acc: "border-acc/40 bg-acc/15 text-[#b3a8ff]",
  mint: "border-mint/40 bg-mint/10 text-mint",
  danger: "border-danger/40 bg-danger/10 text-danger",
  amber: "border-amber/40 bg-amber/10 text-amber",
  sky: "border-acc2/40 bg-acc2/10 text-acc2",
  violet: "border-fuchsia-400/40 bg-fuchsia-400/10 text-fuchsia-300",
  dim: "border-edge bg-white/[0.04] text-dim",
};

export function Chip({ tone = "dim", className, children }: { tone?: Tone; className?: string; children: ReactNode }) {
  return (
    <span className={cx("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium", TONES[tone], className)}>
      {children}
    </span>
  );
}

const CAT_META: Record<Category, { label: string; tone: Tone }> = {
  A: { label: "Справка", tone: "sky" },
  B: { label: "Заявка", tone: "acc" },
  C: { label: "Скидка", tone: "mint" },
  D: { label: "КП", tone: "violet" },
  E: { label: "Сложный вопрос", tone: "sky" },
  F: { label: "Инъекция", tone: "danger" },
  G: { label: "Гарантия", tone: "amber" },
  H: { label: "Доставка", tone: "amber" },
  I: { label: "Спор", tone: "danger" },
};

export function CategoryChip({ category, withLabel = true }: { category: Category | null; withLabel?: boolean }) {
  if (!category) return <Chip>нет</Chip>;
  const m = CAT_META[category];
  return <Chip tone={m.tone}><span className="mono font-bold">{category}</span>{withLabel && <span>· {m.label}</span>}</Chip>;
}

const STATUS_META: Record<ConvStatus, { label: string; tone: Tone }> = {
  ACTIVE: { label: "Автономно", tone: "mint" },
  HOLD: { label: "HOLD · ждёт менеджера", tone: "danger" },
  HANDOFF: { label: "Менеджер подключён", tone: "acc" },
  RESOLVED: { label: "Закрыт", tone: "dim" },
};

export function StatusChip({ status }: { status: ConvStatus }) {
  const m = STATUS_META[status];
  return (
    <Chip tone={m.tone} className={status === "HOLD" ? "hold-pulse" : ""}>
      <span className={cx("size-1.5 rounded-full", status === "HOLD" ? "bg-danger blink" : status === "ACTIVE" ? "bg-mint" : status === "HANDOFF" ? "bg-acc" : "bg-dim")} />
      {m.label}
    </Chip>
  );
}

export function Kpi({ label, value, sub, tone = "acc", className }: { label: string; value: ReactNode; sub?: ReactNode; tone?: Tone; className?: string }) {
  const valueTone =
    tone === "mint" ? "text-mint" : tone === "danger" ? "text-danger" : tone === "amber" ? "text-amber" : tone === "sky" ? "text-acc2" : tone === "violet" ? "text-fuchsia-300" : tone === "dim" ? "text-txt" : "text-[#b3a8ff]";
  return (
    <div className={cx("glass glass-hover px-4 py-3.5", className)}>
      <div className="text-[10px] uppercase tracking-[0.18em] text-dim">{label}</div>
      <div className={cx("mono mt-1 text-[26px] font-semibold leading-none", valueTone)}>{value}</div>
      {sub && <div className="mt-1.5 text-[11px] leading-snug text-dim">{sub}</div>}
    </div>
  );
}

export function HBar({ label, value, max, tone = "acc", suffix = "" }: { label: string; value: number; max: number; tone?: Tone; suffix?: string }) {
  const w = max > 0 ? Math.max(3, Math.round((value / max) * 100)) : 0;
  const bar =
    tone === "mint" ? "bg-mint/70" : tone === "danger" ? "bg-danger/70" : tone === "amber" ? "bg-amber/70" : tone === "sky" ? "bg-acc2/70" : tone === "violet" ? "bg-fuchsia-400/70" : tone === "dim" ? "bg-dim/50" : "bg-acc/70";
  return (
    <div className="flex items-center gap-3">
      <div className="w-40 shrink-0 truncate text-[11px] text-dim">{label}</div>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.06]">
        <div className={cx("h-full rounded-full transition-all duration-700", bar)} style={{ width: `${w}%` }} />
      </div>
      <div className="mono w-14 shrink-0 text-right text-[11px] text-txt/80">
        {value}
        {suffix}
      </div>
    </div>
  );
}

export function Radial({ value, size = 104, label }: { value: number; size?: number; label?: string }) {
  const r = (size - 14) / 2;
  const c = 2 * Math.PI * r;
  const off = c - (Math.min(100, Math.max(0, value)) / 100) * c;
  const good = value >= 90;
  return (
    <div className="relative grid place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,255,255,.07)" strokeWidth={7} />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none"
          stroke={good ? "#3ce9b4" : "#ffb020"} strokeWidth={7} strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={off}
          style={{ transition: "stroke-dashoffset .9s cubic-bezier(.2,.7,.2,1)", filter: `drop-shadow(0 0 6px ${good ? "rgba(60,233,180,.5)" : "rgba(255,176,32,.5)"})` }}
        />
      </svg>
      <div className="absolute text-center">
        <div className="mono text-xl font-semibold">{value}%</div>
        {label && <div className="px-3 text-[9px] uppercase tracking-wider text-dim">{label}</div>}
      </div>
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] uppercase tracking-wider text-dim">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[10px] text-dim/70">{hint}</span>}
    </label>
  );
}

export const inputCls =
  "w-full rounded-lg border border-edge bg-white/[0.04] px-3 py-2 text-[13px] text-txt outline-none transition placeholder:text-dim/60 focus:border-acc/60 focus:bg-white/[0.06]";

export function Btn({
  children, onClick, tone = "acc", disabled, className, title,
}: { children: ReactNode; onClick?: () => void; tone?: "acc" | "danger" | "mint" | "amber" | "ghost"; disabled?: boolean; className?: string; title?: string }) {
  const styles = {
    acc: "bg-acc/80 text-white hover:bg-acc shadow-[0_8px_24px_-10px_rgba(124,108,255,.8)]",
    mint: "bg-mint/15 text-mint border border-mint/40 hover:bg-mint/25",
    danger: "bg-danger/15 text-danger border border-danger/40 hover:bg-danger/25",
    amber: "bg-amber/15 text-amber border border-amber/40 hover:bg-amber/25",
    ghost: "border border-edge text-dim hover:text-txt hover:border-acc/40",
  }[tone];
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cx("inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-[12.5px] font-medium transition disabled:cursor-not-allowed disabled:opacity-40", styles, className)}
    >
      {children}
    </button>
  );
}
